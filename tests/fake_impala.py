"""Runner falso de Impala: ejecuta el SQL de los repos sobre sqlite3 en memoria.

Viable porque los repos generan SQL ANSI (INSERT VALUES, SELECT, window
functions) con placeholders `?`, que sqlite3 soporta nativo. El nombre
calificado `proceso_enmascarado.guispk_*` se resuelve con ATTACH de una BD en
memoria bajo ese alias. Del DDL de Impala solo hay que quitar
`STORED AS PARQUET`.
"""

import re
import sqlite3
import threading

from core.store import ddl


class FakeImpalaRunner:
    def __init__(self, schema: str = ddl.DEFAULT_SCHEMA):
        # check_same_thread=False + lock: el test de carreras usa hilos.
        self._con = sqlite3.connect(":memory:", check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute(f"ATTACH ':memory:' AS {schema}")
        self._lock = threading.Lock()

    @staticmethod
    def _normalize(sql: str) -> str:
        return re.sub(r"STORED\s+AS\s+PARQUET", "", sql, flags=re.IGNORECASE)

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            cur = self._con.execute(self._normalize(sql), params)
            return [dict(r) for r in cur.fetchall()]

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._con.execute(self._normalize(sql), params)
            self._con.commit()


def new_runner_with_schema() -> FakeImpalaRunner:
    """Runner con las tablas guispk_* ya creadas (equivale a ensure_remote_schema)."""
    runner = FakeImpalaRunner()
    ddl.ensure_remote_schema(runner)
    return runner
