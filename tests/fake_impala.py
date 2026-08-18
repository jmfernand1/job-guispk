"""Runner falso de Impala: ejecuta el SQL de los repos sobre sqlite3 en memoria.

Viable porque los repos generan SQL ANSI (INSERT VALUES, SELECT, window
functions) con placeholders `?`, que sqlite3 soporta nativo. El nombre
calificado `proceso_enmascarado.guispk_*` se resuelve con ATTACH de una BD en
memoria bajo ese alias. Del DDL de Impala hay que quitar `STORED AS PARQUET`
y traducir `STRING` a `TEXT`.

Lo de STRING->TEXT no es cosmetico: SQLite le da afinidad **NUMERIC** a un tipo
que no reconoce, asi que guarda como float un id que resulte ser todo digitos
(pasa ~1 de cada 350 ids: `new_id` = timestamp + 12 hex al azar). Despues el
`sorted` del fold compara float con str y revienta. Con TEXT la afinidad es la
correcta y los ids se guardan como los manda el repo.
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
        sql = re.sub(r"STORED\s+AS\s+PARQUET", "", sql, flags=re.IGNORECASE)
        if re.match(r"\s*CREATE\s+TABLE", sql, flags=re.IGNORECASE):
            # Solo en el DDL: en un INSERT, "STRING" puede ser parte de un
            # script guardado y no hay que tocarlo.
            sql = re.sub(r"\bSTRING\b", "TEXT", sql, flags=re.IGNORECASE)
        return sql

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
