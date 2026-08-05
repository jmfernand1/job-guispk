"""Import unico del SQLite compartido hacia las tablas guispk_* de Impala.

Lo corre UNA persona del equipo interno (necesita Sparky) tras coordinar el
corte con los aliados:

    python -m tools.migrate_sqlite_to_impala <ruta/guispk.db> [--dry-run]

Que migra y que no:
- Inventario completo (alta + desactivacion si aplica).
- SOLO la ultima captura de esquema por tabla (las viejas no se usan).
- Historico de scripts completo: es lo que permite re-ejecutar corridas.
- Solicitudes en estado FINAL (ejecutada / rechazada), replayadas como la
  secuencia de eventos que las habria producido. Las pendientes (borrador /
  enviada) NO se migran: los aliados las recrean tras el corte.

Despues de migrar, el guispk.db se archiva como solo-lectura; no se borra.
Credenciales de Sparky: env vars USERNAME / PSWD / DSNLZ (como la app).
"""

import argparse
import sys

from core import models, states
from core.store import ddl
from core.store.db import open_db

_EVENT_COLS = (
    "event_id, request_id, code, at, who, role, event_type, from_state, "
    "to_state, requester, item_id, fields_final_json, comment, "
    "execution_log, executed_partition_where"
)
_FINAL_STATES = (states.EJECUTADA, states.RECHAZADA)


def _insert(runner, table, cols, values):
    placeholders = ", ".join("?" for _ in values)
    runner.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", values)


def _event(runner, table, **values):
    row = dict.fromkeys(
        (
            "event_id", "request_id", "code", "at", "who", "role", "event_type",
            "from_state", "to_state", "requester", "item_id",
            "fields_final_json", "comment", "execution_log",
            "executed_partition_where",
        )
    )
    row["event_id"] = models.new_id()
    row.update(values)
    _insert(runner, table, _EVENT_COLS, tuple(row.values()))


def migrate(db_path: str, runner, schema: str = ddl.DEFAULT_SCHEMA, log=print) -> dict:
    """Replaya el SQLite como eventos. Devuelve contadores por seccion."""
    inv_t = ddl.qname("inventory_events", schema)
    cap_t = ddl.qname("schema_captures", schema)
    items_t = ddl.qname("request_items", schema)
    events_t = ddl.qname("request_events", schema)
    hist_t = ddl.qname("script_history", schema)
    counts = {"inventario": 0, "capturas": 0, "solicitudes": 0,
              "omitidas_pendientes": 0, "historico": 0}

    with open_db(db_path) as con:
        # -- inventario: alta (+ desactivacion si estaba inactiva) ------------
        inventory = {r["id"]: dict(r) for r in con.execute("SELECT * FROM inventory")}
        for inv in inventory.values():
            _insert(
                runner, inv_t,
                "event_id, at, who, event_type, table_name, description",
                (models.new_id(), inv["added_at"], inv["added_by"], "add",
                 inv["table_name"], inv["description"]),
            )
            if not inv["active"]:
                _insert(
                    runner, inv_t,
                    "event_id, at, who, event_type, table_name, description",
                    (models.new_id(), models.utcnow_iso(), "migracion",
                     "deactivate", inv["table_name"], None),
                )
            counts["inventario"] += 1

        # -- ultima captura por tabla -----------------------------------------
        capture_id_map = {}  # id SQLite -> id nuevo (para request_items)
        rows = con.execute(
            """
            SELECT s.* FROM table_schemas s
            JOIN (
              SELECT inventory_id, MAX(captured_at || printf('%012d', id)) AS mx
              FROM table_schemas GROUP BY inventory_id
            ) last ON last.inventory_id = s.inventory_id
                  AND (s.captured_at || printf('%012d', s.id)) = last.mx
            """
        ).fetchall()
        for cap in rows:
            new_id = models.new_id()
            capture_id_map[cap["id"]] = new_id
            table_name = inventory[cap["inventory_id"]]["table_name"]
            _insert(
                runner, cap_t,
                "capture_id, table_name, captured_at, captured_by, columns_json, "
                "partition_cols_json, last_partition_where, last_ingest",
                (new_id, table_name, cap["captured_at"], cap["captured_by"],
                 cap["columns_json"], cap["partition_cols_json"],
                 cap["last_partition_where"], cap["last_ingest"]),
            )
            counts["capturas"] += 1

        # -- solicitudes en estado final, como secuencia de eventos -----------
        for req in con.execute("SELECT * FROM requests").fetchall():
            if req["state"] not in _FINAL_STATES:
                counts["omitidas_pendientes"] += 1
                log(f"  omitida (pendiente {req['state']}): {req['code']}")
                continue
            request_id = models.new_id()
            _event(
                runner, events_t, request_id=request_id, code=req["code"],
                at=req["created_at"], who=req["requester"],
                role=states.ROLE_ALIADO, event_type="crear",
                to_state=states.BORRADOR, requester=req["requester"],
            )
            sent_at = req["sent_at"] or req["created_at"]
            _event(
                runner, events_t, request_id=request_id, code=req["code"],
                at=sent_at, who=req["requester"], role=states.ROLE_ALIADO,
                event_type="transicion", from_state=states.BORRADOR,
                to_state=states.ENVIADA,
            )
            for item in con.execute(
                "SELECT * FROM request_items WHERE request_id = ? ORDER BY id",
                (req["id"],),
            ):
                item_id = models.new_id()
                _insert(
                    runner, items_t,
                    "item_id, request_id, src_table, dest_table, "
                    "schema_capture_id, fields_json, partition_where_requested, "
                    "sql_preview, created_at",
                    (item_id, request_id, item["src_table"], item["dest_table"],
                     capture_id_map.get(item["schema_capture_id"]),
                     item["fields_json"], item["partition_where_requested"],
                     item["sql_preview"], req["created_at"]),
                )
                if item["fields_final_json"]:
                    _event(
                        runner, events_t, request_id=request_id,
                        code=req["code"], at=req["reviewed_at"] or sent_at,
                        who=req["reviewed_by"] or "migracion",
                        role=states.ROLE_INTERNO,
                        event_type="decision_enmascaramiento", item_id=item_id,
                        fields_final_json=item["fields_final_json"],
                    )
            if req["state"] == states.EJECUTADA:
                _event(
                    runner, events_t, request_id=request_id, code=req["code"],
                    at=req["executed_at"], who=req["executed_by"],
                    role=states.ROLE_INTERNO, event_type="transicion",
                    from_state=states.ENVIADA, to_state=states.EJECUTADA,
                    comment=req["review_comment"],
                    execution_log=req["execution_log"],
                    executed_partition_where=req["executed_partition_where"],
                )
            else:
                _event(
                    runner, events_t, request_id=request_id, code=req["code"],
                    at=req["reviewed_at"], who=req["reviewed_by"],
                    role=states.ROLE_INTERNO, event_type="transicion",
                    from_state=states.ENVIADA, to_state=states.RECHAZADA,
                    comment=req["review_comment"],
                )
            counts["solicitudes"] += 1

        # -- historico de scripts completo ------------------------------------
        for h in con.execute("SELECT * FROM script_history ORDER BY id"):
            _insert(
                runner, hist_t,
                "script_id, at, who, origin, request_code, src_table, "
                "dest_table, partition_where, salt_label, script, status, error",
                (models.new_id(), h["at"], h["who"], h["origin"],
                 h["request_code"], h["src_table"], h["dest_table"],
                 h["partition_where"], h["salt_label"], h["script"],
                 h["status"], h["error"]),
            )
            counts["historico"] += 1

    return counts


class _DryRunRunner:
    """Cuenta INSERTs sin tocar Impala."""

    def __init__(self):
        self.inserts = 0

    def execute(self, sql, params=()):
        self.inserts += 1

    def query(self, sql, params=()):
        return []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", help="ruta al guispk.db compartido")
    parser.add_argument("--schema", default=ddl.DEFAULT_SCHEMA)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="solo cuenta lo que migraria, sin conectar a Impala",
    )
    args = parser.parse_args()

    if args.dry_run:
        runner = _DryRunRunner()
    else:
        from interno.sparky_client import SparkyClient, credentials_from_env
        from interno.sparky_runner import SparkyRunner

        creds = credentials_from_env()
        if not all(creds.values()):
            sys.exit("Faltan credenciales: define USERNAME, PSWD y DSNLZ.")
        client = SparkyClient()
        print(f"Conectando a Sparky (DSN {creds['dsn']})...")
        client.connect(creds["username"], creds["password"], creds["dsn"])
        runner = SparkyRunner(client)
        print("Asegurando esquema guispk_*...")
        ddl.ensure_remote_schema(runner, args.schema)

    print(f"Migrando {args.db_path} -> {args.schema}.guispk_* ...")
    counts = migrate(args.db_path, runner, args.schema)
    for k, v in counts.items():
        print(f"  {k}: {v}")
    if args.dry_run:
        print(f"(dry-run: {runner.inserts} INSERTs se habrian ejecutado)")
    else:
        print("Listo. Archiva el guispk.db como solo-lectura; no lo borres.")


if __name__ == "__main__":
    main()
