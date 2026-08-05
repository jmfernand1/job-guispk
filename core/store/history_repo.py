"""Historico de scripts ejecutados por el equipo interno, sobre Impala.

Guarda el script con los placeholders {{TEXT_SALT}} / {{INT_SALT}} intactos:
las tablas de coordinacion las leen los aliados y los salts reales nunca se
escriben ahi. Al re-ejecutar, la app interna vuelve a sustituirlos con los
salts locales. La tabla ya era insert-only en SQLite: el port es directo.
"""

from core import models
from core.store import ddl

ORIGIN_SOLICITUD = "solicitud"
ORIGIN_ADHOC = "adhoc"
ORIGIN_REEJECUCION = "re-ejecucion"

STATUS_OK = "ok"
STATUS_ERROR = "error"


class HistoryRepo:
    def __init__(self, runner, schema: str = ddl.DEFAULT_SCHEMA):
        self._runner = runner
        self._table = ddl.qname("script_history", schema)

    def record(
        self,
        who: str,
        origin: str,
        src_table: str,
        dest_table: str,
        script: str,
        status: str = STATUS_OK,
        request_code=None,
        partition_where=None,
        salt_label=None,
        error=None,
    ) -> str:
        script_id = models.new_id()
        self._runner.execute(
            f"INSERT INTO {self._table} (script_id, at, who, origin, "
            "request_code, src_table, dest_table, partition_where, salt_label, "
            "script, status, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                script_id,
                models.utcnow_iso(),
                who,
                origin,
                request_code,
                src_table,
                dest_table,
                partition_where,
                salt_label,
                script,
                status,
                error,
            ),
        )
        return script_id

    def list_scripts(self, search=None, limit: int = 200):
        """Ultimas ejecuciones, opcionalmente filtradas por tabla o solicitud."""
        query = f"SELECT * FROM {self._table}"
        params = []
        if search and search.strip():
            like = f"%{search.strip()}%"
            query += (
                " WHERE src_table LIKE ? OR dest_table LIKE ? "
                "OR request_code LIKE ? OR who LIKE ?"
            )
            params = [like, like, like, like]
        # limit inlined: es un int propio, y el ODBC de Impala no soporta
        # parametros en LIMIT.
        query += f" ORDER BY at DESC, script_id DESC LIMIT {int(limit)}"
        rows = self._runner.query(query, tuple(params))
        for row in rows:
            row["id"] = row["script_id"]
        return rows

    def get(self, script_id: str):
        rows = self._runner.query(
            f"SELECT * FROM {self._table} WHERE script_id = ?", (script_id,)
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["id"] = row["script_id"]
        return row
