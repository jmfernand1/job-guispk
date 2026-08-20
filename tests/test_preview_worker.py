"""El worker de vista previa: que consulta y como resuelve la particion.

Se llama `run()` directamente (sin arrancar el hilo) para observar el SQL que
sale hacia el origen: es lo unico que importa aqui.
"""

import os

import pytest

pytest.importorskip("PyQt6")
pd = pytest.importorskip("pandas")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from interno.workers import PreviewWorker  # noqa: E402


class _Client:
    """Cliente minimo: registra el SELECT y decide si SHOW PARTITIONS falla."""

    def __init__(self, where="ingestion_day = '2026-08-01'", falla_particion=False):
        self.queries = []
        self._where = where
        self._falla = falla_particion

    def get_partition_info(self, tabla):
        if self._falla:
            raise RuntimeError("SHOW PARTITIONS no disponible")
        return ["ingestion_day"], self._where

    def query_df(self, sql):
        self.queries.append(sql)
        return pd.DataFrame([("1", "ana")], columns=["num_doc", "nombre"])


def _correr(worker):
    """Ejecuta el worker y devuelve (resultado, error) de sus señales."""
    salida = {}
    worker.finished.connect(lambda r: salida.update(result=r))
    worker.error.connect(lambda m: salida.update(error=m))
    worker.run()
    return salida


def test_usa_el_where_de_la_solicitud_sin_preguntar_al_origen():
    client = _Client(falla_particion=True)  # explotaria si preguntara
    salida = _correr(
        PreviewWorker(client, "origen.t", ["num_doc", "nombre"], "anio = 2026")
    )
    assert "error" not in salida
    assert client.queries == [
        "SELECT num_doc, nombre\nFROM origen.t\nWHERE anio = 2026\nLIMIT 100;"
    ]


def test_sin_where_resuelve_la_ultima_particion_del_origen():
    client = _Client()
    _correr(PreviewWorker(client, "origen.t", ["num_doc"]))
    assert "WHERE ingestion_day = '2026-08-01'" in client.queries[0]


def test_si_no_hay_particion_consulta_la_tabla_plana():
    client = _Client(falla_particion=True)
    _correr(PreviewWorker(client, "origen.t", ["num_doc"]))
    assert client.queries == ["SELECT num_doc\nFROM origen.t\nLIMIT 100;"]


def test_devuelve_el_dataframe_y_el_sql_ejecutado():
    client = _Client()
    salida = _correr(PreviewWorker(client, "origen.t", ["num_doc"], "anio = 2026"))
    df, sql = salida["result"]
    assert list(df.columns) == ["num_doc", "nombre"]
    assert sql == client.queries[0]


def test_el_limite_es_configurable():
    client = _Client()
    _correr(PreviewWorker(client, "origen.t", ["num_doc"], "anio = 2026", limit=10))
    assert client.queries[0].endswith("LIMIT 10;")


def test_un_fallo_de_consulta_llega_como_error():
    class _Roto(_Client):
        def query_df(self, sql):
            raise RuntimeError("tabla inexistente")

    salida = _correr(PreviewWorker(_Roto(), "origen.t", ["num_doc"], "anio = 2026"))
    assert "tabla inexistente" in salida["error"]
