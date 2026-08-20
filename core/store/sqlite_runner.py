"""Backend SQLite para las tablas `guispk_*`, cuando Impala no esta disponible.

El DSN corporativo tiene intermitencias y, mientras dura una, no se puede ni
consultar el estado de una solicitud ni registrar una nueva. Este runner cumple
el mismo `ImpalaRunner` que los repos ya esperan (`core/store/runner.py`), asi
que apuntar la app a un archivo `.db` no cambia una linea de los repos.

**Es el mismo archivo del respaldo.** Las tablas se crean planas
(`guispk_*`, sin esquema) igual que en `core/store/backup.py`, y el nombre
calificado `proceso_enmascarado.guispk_*` que generan los repos se resuelve con
un ATTACH bajo ese alias. Asi un `.db` de respaldo se abre tal cual, y lo que se
escriba aqui lo sube a Impala el `restore` de siempre: inserta por id lo que
falta, sin borrar ni actualizar.

Lo que **no** hace: consultar tablas de negocio. DESCRIBE, SHOW PARTITIONS,
la vista previa y la ejecucion de scripts siguen necesitando Impala; la UI las
deshabilita mientras el backend sea este.

Del DDL de Impala hay que quitar `STORED AS PARQUET` y traducir `STRING` a
`TEXT`: a un tipo que no reconoce, SQLite le da afinidad NUMERIC y guardaria
como float un id que resulte ser todo digitos, con lo que el `sorted` del fold
compararia float con str y reventaria.
"""

import os
import re
import sqlite3
import threading

from core.store import ddl


class SqliteRunner:
    """Runner sobre un archivo SQLite con el layout del respaldo."""

    def __init__(
        self, db_path: str, schema: str = ddl.DEFAULT_SCHEMA, ensure_schema: bool = True
    ):
        if not db_path or not db_path.strip():
            raise ValueError("Falta la ruta del archivo SQLite.")
        self.db_path = os.path.expanduser(db_path.strip())
        self.schema = schema
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # check_same_thread=False + lock: los repos se llaman desde QThreads
        # (core/ui/repo_worker.py) y comparten esta conexion.
        self._con = sqlite3.connect(":memory:", check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute(f"ATTACH ? AS {schema}", (self.db_path,))
        # journal_mode=DELETE y synchronous=FULL: el archivo suele vivir en
        # OneDrive/SMB, donde WAL no es confiable (misma razon que en backup.py).
        self._con.execute(f"PRAGMA {schema}.journal_mode=DELETE")
        self._con.execute(f"PRAGMA {schema}.synchronous=FULL")
        self._con.execute("PRAGMA busy_timeout=30000")
        self._lock = threading.Lock()
        if ensure_schema:
            ddl.ensure_remote_schema(self, schema)

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

    def close(self) -> None:
        with self._lock:
            self._con.close()
