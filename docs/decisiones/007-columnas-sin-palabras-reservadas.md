# 007 — Ninguna columna de las `guispk_*` se llama como palabra reservada

**Fecha:** 2026-08-06
**Tags:** `#bd` `#sql`
**Modifica:** [006](006-coordinacion-impala-append-only.md) — mismo esquema,
nombres de columna distintos.

## Contexto

Al desplegar el esquema en produccion, el `CREATE TABLE` fallo. Desde Impala
3.0 el parser aplica por defecto la lista de palabras reservadas de ANSI
SQL:2016, y varias columnas del diseno original caian dentro: `at`, `role`,
`comment`. Se podrian escapar con backticks, pero estas tablas las leen los
aliados desde sus propias herramientas: un nombre que obliga a citar en cada
consulta es una trampa permanente, no un detalle de sintaxis.

## Decision

Renombrar en `core/store/ddl.py` y en todo el SQL de los repos:

| Antes | Ahora | Tablas |
|-------|-------|--------|
| `at` | `event_at` | `inventory_events`, `request_events`, `script_history` |
| `who` | `event_by` | `inventory_events`, `request_events`, `script_history` |
| `role` | `actor_role` | `request_events` |
| `comment` | `note` | `request_events` |

`who` no es reservada en Impala; se renombra igual por simetria con `at` y
porque si lo es en otros motores (y las `guispk_*` se consultan desde varias
herramientas).

Los **parametros Python** de los metodos publicos de los repos siguen
llamandose `who`, `role` y `comment`: el renombre es del nombre fisico de
columna, no de la API interna, asi que los llamadores no cambian.

Para que no vuelva a pasar:

- `ddl.RESERVED_WORDS` + `ddl.reserved_columns(...)` en `core/store/ddl.py`.
- `tests/test_ddl_reserved.py` parsea el DDL **real** (no una lista escrita a
  mano) y falla si alguna columna declarada es reservada. Una columna nueva
  queda cubierta sin tocar el test.
- `RequestsRepo._insert_event` rechaza claves desconocidas: antes un nombre
  viejo (`who=...`) se colaba como clave extra y el INSERT moria con un
  "N values for 15 columns" ilegible.

## Consecuencias

- **Rompe compatibilidad con las tablas ya creadas.** No hay ALTER aditivo que
  renombre: donde el esquema exista (dev / QA), hay que `DROP TABLE ... PURGE`
  y volver a crear, o `INSERT ... SELECT` a las tablas nuevas. En produccion no
  aplica: el despliegue nunca llego a crearlas.
- Los `.exe` ya distribuidos escriben con los nombres viejos y **dejan de
  funcionar** contra el esquema nuevo. El cambio exige redistribuir las dos
  apps; se acepta porque el esquema aun no esta en produccion.
- `tools/migrate_sqlite_to_impala.py` escribe a los nombres nuevos; sigue
  leyendo el SQLite viejo con los nombres viejos (`core/store/migrations.py`
  queda intacto: describe la BD de origen, que ya no se modifica).

## Archivos

`core/store/{ddl,requests_repo,catalog_repo,history_repo}.py`,
`interno/ui/main_window.py`, `tools/migrate_sqlite_to_impala.py`,
`tests/{test_ddl_reserved,test_store,test_history}.py`.
