"""Esquema de coordinacion en Impala: tablas guispk_* en proceso_enmascarado.

Todas las tablas son Parquet **insert-only**: Impala no soporta UPDATE sobre
Parquet, asi que el estado vigente se deriva plegando eventos (ver
requests_repo). Ningun contenido lleva secretos: los scripts se guardan con
placeholders {{TEXT_SALT}} / {{INT_SALT}}.

`ensure_remote_schema` la ejecuta SOLO la app interna (CREATE TABLE IF NOT
EXISTS es idempotente); los aliados operan con GRANT SELECT + INSERT sobre
estas tablas y nunca crean esquema.

A diferencia de las migraciones SQLite (PRAGMA user_version), aqui no hay
versionado: cambios futuros deben ser aditivos via ALTER TABLE ADD COLUMNS,
agregados al final de _ALTERS y nunca editados una vez publicados.

Ningun nombre de columna puede ser palabra reservada de Impala (ANSI SQL:2016):
el CREATE TABLE falla al desplegar. Por eso `at`/`who`/`role`/`comment` son
`event_at`/`event_by`/`actor_role`/`note`. RESERVED_WORDS + el test que lo
verifica existen para que un campo nuevo no vuelva a romper el despliegue.
"""

DEFAULT_SCHEMA = "proceso_enmascarado"
PREFIX = "guispk_"

# Palabras reservadas de Impala (lista ANSI SQL:2016, default desde Impala 3.0)
# que un nombre de columna razonable podria pisar. No es la lista completa: son
# las que un campo de negocio tenderia a usar. Ampliar si aparece otra.
RESERVED_WORDS = frozenset(
    """
    all alter and any array as asc at authorization between bigint binary
    boolean both by cascade case cast char column comment commit create cross
    cube current current_date current_time current_timestamp current_user cursor
    date day dec decimal default delete desc describe distinct double drop else
    end escape exists external false fetch filter first float following for
    foreign from full function grant group grouping having hour if in inner
    insert int integer intersect interval into is join language last left like
    limit local location match merge minute month natural no not null nulls of
    offset on only or order out outer over overwrite partition position
    precision primary range real references rename replace restrict revoke
    right role rollback rollup row rows schema select set show similar smallint
    some start stored string table then time timestamp tinyint to trailing true
    truncate union unique unknown update use user using values varchar view when
    where window with year
    """.split()
)


def reserved_columns(columns) -> list[str]:
    """Devuelve las columnas cuyo nombre es palabra reservada de Impala."""
    return [c for c in columns if c.lower() in RESERVED_WORDS]


def qname(name: str, schema: str = DEFAULT_SCHEMA) -> str:
    """Nombre calificado de una tabla de coordinacion: schema.guispk_<name>."""
    return f"{schema}.{PREFIX}{name}"


# El esquema, declarado como datos y no como texto SQL: de aqui salen tanto el
# CREATE TABLE de Impala como el espejo SQLite del respaldo (core/store/backup.py)
# y el test de palabras reservadas. Una sola fuente de verdad.
#
# `id` es la columna que identifica la fila de forma unica: el restore la usa
# para no duplicar al reinsertar. Todas las columnas son STRING (los repos
# serializan a texto; los ids son UUID y las fechas ISO-8601).
SCHEMA = {
    "inventory_events": {
        "doc": "Inventario de tablas autorizadas: vigente = ultimo evento por table_name.",
        "id": "event_id",
        "columns": (
            "event_id", "event_at", "event_by", "event_type", "table_name",
            "description",
        ),
    },
    "schema_captures": {
        "doc": "Capturas DESCRIBE: ya era append-only; ultima captura por table_name.",
        "id": "capture_id",
        "columns": (
            "capture_id", "table_name", "captured_at", "captured_by",
            "columns_json", "partition_cols_json", "last_partition_where",
            "last_ingest",
        ),
    },
    "request_items": {
        "doc": "Items de solicitud: inmutables; la decision del interno va como evento.",
        "id": "item_id",
        "columns": (
            "item_id", "request_id", "src_table", "dest_table",
            "schema_capture_id", "fields_json", "partition_where_requested",
            "sql_preview", "created_at",
        ),
    },
    "request_events": {
        "doc": "Eventos de solicitud (crear / transicion / decision). El estado de "
               "la solicitud es el fold de sus eventos; son tambien la auditoria.",
        "id": "event_id",
        "columns": (
            "event_id", "request_id", "code", "event_at", "event_by",
            "actor_role", "event_type", "from_state", "to_state", "requester",
            "item_id", "fields_final_json", "note", "execution_log",
            "executed_partition_where",
        ),
    },
    "script_history": {
        "doc": "Historico de scripts ejecutados (con placeholders de salt, re-ejecutable).",
        "id": "script_id",
        "columns": (
            "script_id", "event_at", "event_by", "origin", "request_code",
            "src_table", "dest_table", "partition_where", "salt_label",
            "script", "status", "error",
        ),
    },
}


def all_columns():
    """Todas las columnas declaradas, tabla por tabla (para checks de esquema)."""
    return [c for spec in SCHEMA.values() for c in spec["columns"]]


def _ddl(schema: str) -> list[str]:
    stmts = []
    for name, spec in SCHEMA.items():
        cols = ",\n          ".join(f"{c} STRING" for c in spec["columns"])
        stmts.append(
            f"""
        CREATE TABLE IF NOT EXISTS {qname(name, schema)} (
          {cols}
        ) STORED AS PARQUET
        """
        )
    return stmts


# Cambios aditivos posteriores a la publicacion inicial (ALTER TABLE ... ADD
# COLUMNS). Deben tolerar re-ejecucion (Impala: IF NOT EXISTS no existe para
# columnas; envolver en try/except en ensure_remote_schema si se agregan).
_ALTERS: list[str] = []


def ensure_remote_schema(runner, schema: str = DEFAULT_SCHEMA):
    """Crea las tablas de coordinacion si no existen. Solo la app interna."""
    for stmt in _ddl(schema):
        runner.execute(stmt)
    for stmt in _ALTERS:
        try:
            runner.execute(stmt)
        except Exception:
            pass  # columna ya agregada por una corrida anterior
