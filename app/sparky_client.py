"""Adaptador fino sobre la libreria interna Sparky.

Aisla la dependencia interna para que la UI y los tests no dependan de ella
directamente. En tests se inyecta un stub (fake_sparky) via `sparky_factory`.
"""

import os


class SparkyClient:
    """Envuelve Sparky: conectar, describe, ejecutar query / archivo."""

    def __init__(self, sparky_factory=None):
        # sparky_factory(username, password, dsn) -> objeto tipo Sparky.
        # Por defecto importa la Sparky real de forma perezosa (solo al conectar).
        self._factory = sparky_factory
        self._spk = None
        self._lz = None

    # -- conexion -----------------------------------------------------------
    def _default_factory(self, username, password, dsn):
        from sparky_bc import Sparky  # import perezoso: solo en entorno real

        return Sparky(username=username, password=password, dsn=dsn)

    def connect(self, username, password, dsn):
        factory = self._factory or self._default_factory
        self._spk = factory(username, password, dsn)
        self._lz = self._spk.helper
        return self._spk

    @property
    def connected(self) -> bool:
        return self._spk is not None

    def _ensure(self):
        if self._spk is None:
            raise RuntimeError("No conectado a Sparky. Conecta primero.")

    # -- operaciones --------------------------------------------------------
    def get_partition(self, tabla):
        """Obtiene los campos de particion de una tabla"""
        self._ensure()
        df = self._lz.obtener_dataframe(f"SHOW PARTITIONS {tabla}")
        cols_antes = df.columns[:df.columns.get_loc('#Rows')].tolist()
        df = df[cols_antes].copy()
        df = df.iloc[:-1].tail(1)
        filt = df.to_dict(orient='records')
        where_clause = ' and '.join(
            f"{k} = {v}" for k, v in filt[0].items()
            )
        return where_clause

    
    def last_ingest(self, tabla):
        """Obtiene la ultima ingestion de la tabla consultada"""
        self._ensure()
        return self._lz.obtener_ultima_ingestion(f"{tabla}")
    
    def describe(self, tabla):
        """DESCRIBE <tabla> -> pandas.DataFrame con columnas name|type|comment."""
        self._ensure()
        return self._lz.obtener_dataframe(f"DESCRIBE {tabla}")

    def run(self, query):
        """Ejecuta una sola query."""
        self._ensure()
        return self._lz.ejecutar_consulta(query)

    def run_file(self, path):
        """Ejecuta un archivo .sql con varias sentencias."""
        self._ensure()
        return self._lz.ejecutar_archivo(path)


def credentials_from_env():
    """Lee credenciales por defecto desde las env vars del usuario."""
    return {
        "username": os.getenv("USERNAME", ""),
        "password": os.getenv("PSWD", ""),
        "dsn": os.getenv("DSNLZ", ""),
    }
