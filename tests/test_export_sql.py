"""Exportar a .sql el SQL de una solicitud, para editarlo y correrlo aparte.

Lo que importa: que el archivo exportado sea **el mismo SQL que se ejecutaria**
(misma decision de enmascaramiento, misma particion re-resuelta) y que salga
ejecutable, sin placeholders de salt sin sustituir.
"""

import pytest

from core import masking, sql_builder

REQUESTED = [
    {"col": "id_cliente", "type": "bigint"},
    {"col": "nombre", "type": "string"},
    {"col": "ingestion_day", "type": "string"},
]
FINAL = [
    {"col": "id_cliente", "type": "bigint", "masking": masking.MASK_INT},
    {"col": "nombre", "type": "string", "masking": masking.MASK_TEXT},
    {"col": "ingestion_day", "type": "string", "masking": masking.NONE},
]
ITEM = {
    "src_table": "origen.clientes",
    "dest_table": "proceso_enmascarado.clientes_enm",
    "fields": REQUESTED,
    "partition_where_requested": "ingestion_day = '2026-07-01'",
}


class FakeClient:
    """Devuelve una particion fresca, o revienta si `falla`."""

    def __init__(self, falla=False):
        self.falla = falla
        self.calls = []

    def get_partition_info(self, tabla):
        self.calls.append(tabla)
        if self.falla:
            raise RuntimeError("sin conexion")
        return ["ingestion_day"], "ingestion_day = '2026-08-01'"


# ---------------------------------------------------------------- documento


def test_export_lleva_cabecera_y_un_bloque_por_tabla():
    doc = sql_builder.build_export_script(
        [("origen.a -> destino.a", "SELECT 1;"), ("origen.b -> destino.b", "SELECT 2;")],
        ["Solicitud SOL-1", "Salt aplicado: 2026-08"],
    )
    assert "-- Solicitud SOL-1" in doc
    assert "-- Salt aplicado: 2026-08" in doc
    assert "-- ===== origen.a -> destino.a =====" in doc
    assert "-- ===== origen.b -> destino.b =====" in doc
    assert doc.index("SELECT 1;") < doc.index("SELECT 2;")
    assert doc.endswith("\n")


def test_export_sin_cabecera_no_abre_con_lineas_vacias():
    doc = sql_builder.build_export_script([("t", "SELECT 1;")])
    assert doc.startswith("-- ===== t =====")


# ---------------------------------------------------------------- particion


def test_resolve_partition_sin_particion_no_consulta():
    workers = pytest.importorskip("interno.workers")
    client = FakeClient()
    item = dict(ITEM, partition_where_requested=None)
    assert workers.resolve_partition(client, item) == (None, None, None)
    assert client.calls == []


def test_resolve_partition_reresuelve_contra_impala():
    workers = pytest.importorskip("interno.workers")
    part_cols, where, aviso = workers.resolve_partition(FakeClient(), ITEM)
    assert part_cols == ["ingestion_day"]
    assert where == "ingestion_day = '2026-08-01'"
    assert aviso is None


def test_resolve_partition_cae_al_where_de_la_solicitud():
    workers = pytest.importorskip("interno.workers")
    part_cols, where, aviso = workers.resolve_partition(FakeClient(falla=True), ITEM)
    assert part_cols is None
    assert where == ITEM["partition_where_requested"]
    assert "no se pudo re-resolver la particion" in aviso


# ---------------------------------------------------------------- el worker


def _build(client, items, decisions):
    """Corre BuildScriptsWorker en el hilo actual y devuelve (resultado, error)."""
    workers = pytest.importorskip("interno.workers")
    worker = workers.BuildScriptsWorker(client, {"items": items}, decisions)
    salida = {}
    worker.finished.connect(lambda r: salida.update(ok=r))
    worker.error.connect(lambda m: salida.update(error=m))
    worker.run()
    return salida.get("ok"), salida.get("error")


def test_worker_arma_el_sql_con_la_particion_fresca():
    resultado, error = _build(FakeClient(), [ITEM], [FINAL])
    assert error is None
    (item,) = resultado
    assert item["partition_where"] == "ingestion_day = '2026-08-01'"
    assert "PARTITIONED BY" in item["script"]
    assert "ingestion_day = '2026-08-01'" in item["script"]
    assert item["aviso"] is None


def test_worker_devuelve_el_mismo_sql_que_se_ejecutaria():
    """El .sql exportado tiene que ser identico al que arma la ejecucion."""
    resultado, _ = _build(FakeClient(), [ITEM], [FINAL])
    esperado = sql_builder.build_request_script(
        FINAL,
        ITEM["src_table"],
        ITEM["dest_table"],
        "ingestion_day = '2026-08-01'",
        ["ingestion_day"],
    )[3]
    assert resultado[0]["script"] == esperado


def test_worker_no_deja_salts_en_el_script_pero_el_archivo_sale_ejecutable():
    resultado, _ = _build(FakeClient(), [ITEM], [FINAL])
    script = resultado[0]["script"]
    assert masking.TEXT_SALT_PLACEHOLDER in script
    assert masking.INT_SALT_PLACEHOLDER in script

    doc = masking.apply_salts(
        sql_builder.build_export_script([("t", script)], ["Salt aplicado: 2026-08"]),
        "sal-de-texto",
        7,
    )
    assert masking.TEXT_SALT_PLACEHOLDER not in doc
    assert masking.INT_SALT_PLACEHOLDER not in doc
    assert "sal-de-texto" in doc


def test_worker_rechaza_una_columna_que_no_se_pidio():
    intruso = FINAL + [{"col": "saldo", "type": "decimal(12,2)", "masking": masking.NONE}]
    resultado, error = _build(FakeClient(), [ITEM], [intruso])
    assert resultado is None
    assert "no fue solicitada" in error


def test_worker_avisa_si_no_pudo_re_resolver_la_particion():
    resultado, error = _build(FakeClient(falla=True), [ITEM], [FINAL])
    assert error is None
    assert "no se pudo re-resolver la particion" in resultado[0]["aviso"]
    assert "PARTITIONED BY" not in resultado[0]["script"]


def test_worker_falla_si_la_solicitud_cambio_en_pantalla():
    resultado, error = _build(FakeClient(), [ITEM, ITEM], [FINAL])
    assert resultado is None
    assert "cambio en pantalla" in error
