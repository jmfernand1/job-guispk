"""Conexion del aliado a Impala: Sparky primero, ODBC como respaldo.

Los aliados que tienen sparky_bc instalado se conectan por ahi (misma libreria
y mismas credenciales que el equipo interno); los que no —o aquellos a quienes
Sparky les falla— caen automaticamente al DSN ODBC ya provisionado en su
maquina. En los dos casos el aliado solo necesita SELECT + INSERT sobre las
tablas guispk_* de proceso_enmascarado: el esquema lo crea y mantiene la app
interna.

La app aliada sigue sin importar `interno/`: el adaptador de Sparky vive en
`core/` porque lo comparten las dos apps. Los dos imports (sparky_bc, pyodbc)
son perezosos, asi que un aliado sin una de las dos librerias no revienta al
arrancar — simplemente usa la otra.

Los literales se resuelven en el cliente (mismo dialecto que el interno) en
vez de depender del binding de parametros del driver.
"""

import os
import threading

from core.store.runner import inline_params

BACKEND_SPARKY = "Sparky"
BACKEND_ODBC = "ODBC"


class ImpalaOdbcRunner:
    def __init__(self, connection):
        self._con = connection
        self._lock = threading.Lock()  # una conexion, varios RepoWorkers

    @classmethod
    def connect(cls, dsn: str, username: str, password: str):
        import pyodbc  # import perezoso: solo al conectar

        con = pyodbc.connect(
            f"DSN={dsn};UID={username};PWD={password}",
            autocommit=True,
            timeout=30,
        )
        return cls(con)

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            cur = self._con.cursor()
            try:
                cur.execute(inline_params(sql, params))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                cur.close()

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            cur = self._con.cursor()
            try:
                cur.execute(inline_params(sql, params))
            finally:
                cur.close()

    def close(self):
        self._con.close()


def _connect_sparky(dsn: str, username: str, password: str):
    # Import perezoso y dentro de la funcion: si el aliado no tiene sparky_bc
    # instalado, esto lanza ImportError y connect_impala cae a ODBC.
    from core.sparky_client import SparkyClient
    from core.sparky_runner import SparkyRunner

    client = SparkyClient()
    client.connect(username, password, dsn)
    return SparkyRunner(client)


def connect_impala(dsn: str, username: str, password: str, sparky_connect=None):
    """Devuelve (runner, backend, fallback_error).

    Intenta Sparky y, si falla por lo que sea (libreria ausente, login, red),
    reintenta por ODBC. `fallback_error` trae el motivo del intento fallido
    para poder mostrarlo: que el aliado sepa por que quedo en ODBC.

    Si los dos caminos fallan, propaga el error de ODBC (el ultimo intento).
    `sparky_connect` existe para los tests.
    """
    connect = sparky_connect or _connect_sparky
    try:
        return connect(dsn, username, password), BACKEND_SPARKY, None
    except Exception as exc:
        fallback_error = f"{type(exc).__name__}: {exc}"
    runner = ImpalaOdbcRunner.connect(dsn, username, password)
    return runner, BACKEND_ODBC, fallback_error


def credentials_from_env():
    """Prefill de los campos de conexion desde las env vars del aliado."""
    return {
        "username": os.getenv("USERNAME", ""),
        "password": os.getenv("PSWD", ""),
        "dsn": os.getenv("DSNLZ", ""),
    }
