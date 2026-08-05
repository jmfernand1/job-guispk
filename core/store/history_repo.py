"""Historico de scripts ejecutados por el equipo interno.

Guarda el script con los placeholders {{TEXT_SALT}} / {{INT_SALT}} intactos:
la BD es compartida y los salts reales nunca se escriben ahi. Al re-ejecutar,
la app interna vuelve a sustituirlos con los salts locales.
"""

from core import models
from core.store.db import open_db

ORIGIN_SOLICITUD = "solicitud"
ORIGIN_ADHOC = "adhoc"
ORIGIN_REEJECUCION = "re-ejecucion"

STATUS_OK = "ok"
STATUS_ERROR = "error"


class HistoryRepo:
    def __init__(self, db_path: str):
        self._path = db_path

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
    ) -> int:
        with open_db(self._path) as con:
            cur = con.execute(
                "INSERT INTO script_history (at, who, origin, request_code, "
                "src_table, dest_table, partition_where, salt_label, script, "
                "status, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
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
            return cur.lastrowid

    def list_scripts(self, search=None, limit: int = 200):
        """Ultimas ejecuciones, opcionalmente filtradas por tabla o solicitud."""
        query = "SELECT * FROM script_history"
        params = []
        if search and search.strip():
            like = f"%{search.strip()}%"
            query += (
                " WHERE src_table LIKE ? OR dest_table LIKE ? "
                "OR request_code LIKE ? OR who LIKE ?"
            )
            params = [like, like, like, like]
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with open_db(self._path) as con:
            return [dict(r) for r in con.execute(query, params)]

    def get(self, script_id: int):
        with open_db(self._path) as con:
            row = con.execute(
                "SELECT * FROM script_history WHERE id = ?", (script_id,)
            ).fetchone()
            return dict(row) if row else None
