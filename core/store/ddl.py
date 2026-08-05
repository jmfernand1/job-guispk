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
"""

DEFAULT_SCHEMA = "proceso_enmascarado"
PREFIX = "guispk_"


def qname(name: str, schema: str = DEFAULT_SCHEMA) -> str:
    """Nombre calificado de una tabla de coordinacion: schema.guispk_<name>."""
    return f"{schema}.{PREFIX}{name}"


def _ddl(schema: str) -> list[str]:
    q = lambda name: qname(name, schema)
    return [
        # Inventario de tablas autorizadas: vigente = ultimo evento por table_name.
        f"""
        CREATE TABLE IF NOT EXISTS {q('inventory_events')} (
          event_id STRING,
          at STRING,
          who STRING,
          event_type STRING,
          table_name STRING,
          description STRING
        ) STORED AS PARQUET
        """,
        # Capturas DESCRIBE: ya era append-only; ultima captura por table_name.
        f"""
        CREATE TABLE IF NOT EXISTS {q('schema_captures')} (
          capture_id STRING,
          table_name STRING,
          captured_at STRING,
          captured_by STRING,
          columns_json STRING,
          partition_cols_json STRING,
          last_partition_where STRING,
          last_ingest STRING
        ) STORED AS PARQUET
        """,
        # Items de solicitud: inmutables; la decision del interno va como evento.
        f"""
        CREATE TABLE IF NOT EXISTS {q('request_items')} (
          item_id STRING,
          request_id STRING,
          src_table STRING,
          dest_table STRING,
          schema_capture_id STRING,
          fields_json STRING,
          partition_where_requested STRING,
          sql_preview STRING,
          created_at STRING
        ) STORED AS PARQUET
        """,
        # Eventos de solicitud: crear / transicion / decision_enmascaramiento.
        # Estado de la solicitud = fold de sus eventos; tambien es la auditoria.
        f"""
        CREATE TABLE IF NOT EXISTS {q('request_events')} (
          event_id STRING,
          request_id STRING,
          code STRING,
          at STRING,
          who STRING,
          role STRING,
          event_type STRING,
          from_state STRING,
          to_state STRING,
          requester STRING,
          item_id STRING,
          fields_final_json STRING,
          comment STRING,
          execution_log STRING,
          executed_partition_where STRING
        ) STORED AS PARQUET
        """,
        # Historico de scripts ejecutados (con placeholders de salt, re-ejecutable).
        f"""
        CREATE TABLE IF NOT EXISTS {q('script_history')} (
          script_id STRING,
          at STRING,
          who STRING,
          origin STRING,
          request_code STRING,
          src_table STRING,
          dest_table STRING,
          partition_where STRING,
          salt_label STRING,
          script STRING,
          status STRING,
          error STRING
        ) STORED AS PARQUET
        """,
    ]


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
