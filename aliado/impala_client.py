"""Conexion del aliado a Impala via ODBC (DSN corporativo).

La app aliado NUNCA importa sparky_bc ni interno/: su unico camino a Impala
es el DSN ODBC ya provisionado en la maquina del aliado, con sus propias
credenciales. Solo necesita SELECT + INSERT sobre las tablas guispk_* de
proceso_enmascarado; el esquema lo crea y mantiene la app interna.

Los literales se resuelven en el cliente (mismo dialecto que el interno) en
vez de depender del binding de parametros del driver.
"""

import os
import threading

from core.store.runner import inline_params


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


def credentials_from_env():
    """Prefill de los campos de conexion desde las env vars del aliado."""
    return {
        "username": os.getenv("USERNAME", ""),
        "password": os.getenv("PSWD", ""),
        "dsn": os.getenv("DSNLZ", ""),
    }
