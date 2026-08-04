"""Repositorio del catalogo: inventario de tablas autorizadas + capturas DESCRIBE."""

import json

from core import models
from core.store.db import open_db


class CatalogRepo:
    def __init__(self, db_path: str):
        self._path = db_path

    # -- inventario ----------------------------------------------------------
    def add_table(self, table_name: str, description: str, added_by: str) -> int:
        with open_db(self._path) as con:
            cur = con.execute(
                "INSERT INTO inventory (table_name, description, active, added_by, added_at) "
                "VALUES (?, ?, 1, ?, ?)",
                (table_name.strip(), description.strip(), added_by, models.utcnow_iso()),
            )
            return cur.lastrowid

    def set_active(self, inventory_id: int, active: bool):
        with open_db(self._path) as con:
            con.execute(
                "UPDATE inventory SET active = ? WHERE id = ?",
                (1 if active else 0, inventory_id),
            )

    def list_inventory(self, active_only: bool = False):
        query = "SELECT * FROM inventory"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY table_name"
        with open_db(self._path) as con:
            return [dict(r) for r in con.execute(query)]

    # -- capturas de esquema -------------------------------------------------
    def save_schema_capture(
        self,
        inventory_id: int,
        captured_by: str,
        columns,
        partition_cols=None,
        last_partition_where=None,
        last_ingest=None,
    ) -> int:
        with open_db(self._path) as con:
            cur = con.execute(
                "INSERT INTO table_schemas (inventory_id, captured_at, captured_by, "
                "columns_json, partition_cols_json, last_partition_where, last_ingest) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    inventory_id,
                    models.utcnow_iso(),
                    captured_by,
                    models.columns_to_json(columns),
                    json.dumps(partition_cols) if partition_cols else None,
                    last_partition_where,
                    last_ingest,
                ),
            )
            return cur.lastrowid

    def latest_schemas(self):
        """Ultima captura por tabla activa del inventario (para el aliado).

        Tablas sin captura aparecen con capture_id NULL (pendientes de refresh).
        """
        query = """
            SELECT i.id AS inventory_id, i.table_name, i.description,
                   s.id AS capture_id, s.captured_at, s.captured_by,
                   s.columns_json, s.partition_cols_json,
                   s.last_partition_where, s.last_ingest
            FROM inventory i
            LEFT JOIN table_schemas s ON s.id = (
                SELECT s2.id FROM table_schemas s2
                WHERE s2.inventory_id = i.id
                ORDER BY s2.captured_at DESC, s2.id DESC LIMIT 1
            )
            WHERE i.active = 1
            ORDER BY i.table_name
        """
        with open_db(self._path) as con:
            return [dict(r) for r in con.execute(query)]

    def get_capture(self, capture_id: int):
        with open_db(self._path) as con:
            row = con.execute(
                "SELECT s.*, i.table_name FROM table_schemas s "
                "JOIN inventory i ON i.id = s.inventory_id WHERE s.id = ?",
                (capture_id,),
            ).fetchone()
            return dict(row) if row else None
