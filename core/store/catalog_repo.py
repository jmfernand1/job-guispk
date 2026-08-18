"""Repositorio del catalogo sobre Impala: inventario + capturas DESCRIBE.

Append-only: el inventario es una tabla de eventos (add/activate/deactivate)
y lo vigente por tabla es el resultado de plegarlos en orden. Las capturas ya
eran historicas por naturaleza; la vigente es la ultima por table_name.

El identificador de inventario pasa a ser el propio table_name (antes era el
rowid de SQLite): los metodos conservan el parametro `inventory_id` pero
reciben el nombre de la tabla.
"""

import json

from core import models
from core.store import ddl

_EV_ADD = "add"
_EV_ACTIVATE = "activate"
_EV_DEACTIVATE = "deactivate"


class CatalogRepo:
    def __init__(self, runner, schema: str = ddl.DEFAULT_SCHEMA):
        self._runner = runner
        self._inventory_t = ddl.qname("inventory_events", schema)
        self._captures_t = ddl.qname("schema_captures", schema)

    # -- inventario ----------------------------------------------------------
    def _insert_inventory_event(self, event_type, table_name, who, description=None):
        self._runner.execute(
            f"INSERT INTO {self._inventory_t} (event_id, event_at, event_by, "
            "event_type, table_name, description) VALUES (?, ?, ?, ?, ?, ?)",
            (
                models.new_id(),
                models.utcnow_iso(),
                who,
                event_type,
                table_name,
                description,
            ),
        )

    def add_table(self, table_name: str, description: str, added_by: str) -> str:
        name = table_name.strip()
        self._insert_inventory_event(_EV_ADD, name, added_by, description.strip())
        return name

    def set_active(self, inventory_id: str, active: bool, who: str = ""):
        self._insert_inventory_event(
            _EV_ACTIVATE if active else _EV_DEACTIVATE, inventory_id, who
        )

    def _fold_inventory(self):
        rows = self._runner.query(f"SELECT * FROM {self._inventory_t}")
        rows.sort(key=lambda e: (e["event_at"] or "", e["event_id"]))
        tables = {}
        for e in rows:
            name = e["table_name"]
            if e["event_type"] == _EV_ADD:
                if name in tables:
                    continue  # alta duplicada: gana la primera
                tables[name] = {
                    "id": name,
                    "table_name": name,
                    "description": e["description"],
                    "active": 1,
                    "added_by": e["event_by"],
                    "added_at": e["event_at"],
                }
            elif name in tables:
                tables[name]["active"] = (
                    1 if e["event_type"] == _EV_ACTIVATE else 0
                )
        return tables

    def list_inventory(self, active_only: bool = False):
        rows = list(self._fold_inventory().values())
        if active_only:
            rows = [r for r in rows if r["active"]]
        rows.sort(key=lambda r: r["table_name"])
        return rows

    # -- capturas de esquema -------------------------------------------------
    def save_schema_capture(
        self,
        inventory_id: str,
        captured_by: str,
        columns,
        partition_cols=None,
        last_partition_where=None,
        last_ingest=None,
    ) -> str:
        capture_id = models.new_id()
        self._runner.execute(
            f"INSERT INTO {self._captures_t} (capture_id, table_name, "
            "captured_at, captured_by, columns_json, partition_cols_json, "
            "last_partition_where, last_ingest) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                capture_id,
                inventory_id,
                models.utcnow_iso(),
                captured_by,
                models.columns_to_json(columns),
                json.dumps(partition_cols) if partition_cols else None,
                last_partition_where,
                last_ingest,
            ),
        )
        return capture_id

    def _latest_captures(self):
        """Ultima captura por tabla (window function: corre en Impala y SQLite)."""
        rows = self._runner.query(
            f"""
            SELECT * FROM (
              SELECT c.*, ROW_NUMBER() OVER (
                PARTITION BY table_name
                ORDER BY captured_at DESC, capture_id DESC
              ) AS rn
              FROM {self._captures_t} c
            ) t WHERE rn = 1
            """
        )
        return {r["table_name"]: r for r in rows}

    def latest_schemas(self):
        """Ultima captura por tabla activa del inventario (para el aliado).

        Tablas sin captura aparecen con capture_id NULL (pendientes de refresh).
        """
        captures = self._latest_captures()
        result = []
        for inv in self.list_inventory(active_only=True):
            cap = captures.get(inv["table_name"])
            result.append(
                {
                    "inventory_id": inv["id"],
                    "table_name": inv["table_name"],
                    "description": inv["description"],
                    "capture_id": cap["capture_id"] if cap else None,
                    "captured_at": cap["captured_at"] if cap else None,
                    "captured_by": cap["captured_by"] if cap else None,
                    "columns_json": cap["columns_json"] if cap else None,
                    "partition_cols_json": cap["partition_cols_json"] if cap else None,
                    "last_partition_where": cap["last_partition_where"] if cap else None,
                    "last_ingest": cap["last_ingest"] if cap else None,
                }
            )
        return result

    def get_capture(self, capture_id: str):
        rows = self._runner.query(
            f"SELECT * FROM {self._captures_t} WHERE capture_id = ?",
            (capture_id,),
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["id"] = row["capture_id"]
        return row
