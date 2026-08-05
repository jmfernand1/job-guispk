"""Migraciones del esquema via PRAGMA user_version.

Cada entrada de MIGRATIONS lleva la BD de version N a N+1. Nunca se editan
migraciones ya publicadas: se agrega una nueva al final.
"""

_V1 = """
CREATE TABLE inventory (
  id INTEGER PRIMARY KEY,
  table_name TEXT NOT NULL UNIQUE,
  description TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  added_by TEXT NOT NULL,
  added_at TEXT NOT NULL
);

CREATE TABLE table_schemas (
  id INTEGER PRIMARY KEY,
  inventory_id INTEGER NOT NULL REFERENCES inventory(id),
  captured_at TEXT NOT NULL,
  captured_by TEXT NOT NULL,
  columns_json TEXT NOT NULL,
  partition_cols_json TEXT,
  last_partition_where TEXT,
  last_ingest TEXT
);
CREATE INDEX idx_schemas_inventory ON table_schemas(inventory_id, captured_at);

CREATE TABLE requests (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL DEFAULT 'borrador'
    CHECK (state IN ('borrador','enviada','rechazada','ejecutada')),
  requester TEXT NOT NULL,
  created_at TEXT NOT NULL,
  sent_at TEXT,
  reviewed_by TEXT,
  reviewed_at TEXT,
  review_comment TEXT,
  executed_by TEXT,
  executed_at TEXT,
  execution_log TEXT,
  executed_partition_where TEXT
);

CREATE TABLE request_items (
  id INTEGER PRIMARY KEY,
  request_id INTEGER NOT NULL REFERENCES requests(id),
  src_table TEXT NOT NULL,
  dest_table TEXT NOT NULL,
  schema_capture_id INTEGER REFERENCES table_schemas(id),
  fields_json TEXT NOT NULL,
  partition_where_requested TEXT,
  sql_preview TEXT NOT NULL
);
CREATE INDEX idx_items_request ON request_items(request_id);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY,
  request_id INTEGER REFERENCES requests(id),
  at TEXT NOT NULL,
  who TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT
);
"""

# El enmascaramiento pasa a ser decision del interno: fields_json guarda las
# columnas que pidio el aliado y fields_final_json la decision del interno.
# El backfill es correcto para solicitudes viejas: ahi la mascara que eligio el
# aliado si fue la decision final.
_V2 = """
ALTER TABLE request_items ADD COLUMN fields_final_json TEXT;
UPDATE request_items SET fields_final_json = fields_json;
"""

# Historico de scripts ejecutados por el equipo interno, para consultarlos y
# re-ejecutarlos. El script se guarda con placeholders de salt: la BD es
# compartida y nunca debe contener los salts reales.
_V3 = """
CREATE TABLE script_history (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  who TEXT NOT NULL,
  origin TEXT NOT NULL CHECK (origin IN ('solicitud','adhoc','re-ejecucion')),
  request_code TEXT,
  src_table TEXT NOT NULL,
  dest_table TEXT NOT NULL,
  partition_where TEXT,
  salt_label TEXT,
  script TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok','error')),
  error TEXT
);
CREATE INDEX idx_history_at ON script_history(at);
"""

MIGRATIONS = [_V1, _V2, _V3]


def migrate(con):
    """Aplica las migraciones pendientes segun PRAGMA user_version."""
    current = con.execute("PRAGMA user_version").fetchone()[0]
    for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
        con.executescript(script)
        con.execute(f"PRAGMA user_version = {version}")
