# 003 — El enmascaramiento lo decide el interno

**Tags:** `#enmascaramiento` `#seguridad` `#ui-aliado` `#ui-interno` · **Fecha:** 2026-08-05 · **Commit:** `70575f1`

## Contexto

Al principio el aliado elegia las columnas **y** la mascara de cada una: generaba el SQL
con salts placeholder y lo mandaba. El interno solo podia ejecutar ese SQL tal cual o
rechazar la solicitud entera.

Eso deja el control de que se enmascara en manos de quien pide los datos. Decidir que sale
enmascarado es un punto de control del equipo interno.

## Decision

Se invierte el reparto:

- **Aliado**: solo marca las columnas que necesita. El combo de enmascaramiento
  desaparece de su pantalla y su solicitud pasa a ser una lista de columnas, no SQL.
- **Interno**: sobre esas columnas elige la mascara de cada una y puede **excluir** las
  que no deban salir ni enmascaradas. Todo en la misma pantalla desde la que ejecuta o
  rechaza (coherente con [002](002-sin-paso-de-aprobacion.md)).

En la BD quedan separados los dos momentos:

| Campo | Quien lo escribe | Contenido |
|---|---|---|
| `request_items.fields_json` | aliado | `[{col, type}]` — lo que pidio |
| `request_items.fields_final_json` | interno | `[{col, type, masking}]` — lo que decidio |

El interno **solo puede restringir**. Antes de ejecutar, `core/review.py` valida que cada
columna que se va a crear haya sido solicitada por el aliado y conserve su tipo; agregar
una columna que el aliado nunca pidio es un error, no una opcion.

## Consecuencias

- La decision se persiste **antes** de ejecutar y queda auditada
  (`audit_log.action = 'decision_enmascaramiento'`): hay registro aunque la ejecucion
  falle, y al recargar la pantalla el interno recupera su seleccion en vez de rehacerla.
- El chequeo anti-manipulacion cambio de forma: antes se regeneraba el SQL del aliado y se
  comparaba; ahora se regenera la **vista previa de la solicitud** (que incluye origen,
  destino y WHERE, no solo columnas) y se compara con lo guardado.
- El aliado ya no exporta `.sql` sino un `.txt` con el detalle, y una vez ejecutada la
  solicitud ve que mascara se aplico a cada columna y cuales quedaron excluidas.
- `mask_int` sobre una columna `string` genera SQL valido pero inutil (`cast` → NULL). Se
  avisa en el dialogo, no se bloquea: el interno es el punto de control.

## Archivos

- `core/review.py` — la regla completa (`validate_final_fields`, `excluded_columns`).
- `core/sql_builder.py` — `build_request_preview`.
- `core/ui/column_table.py` — `with_masking=False` para el aliado, `load_fields`.
- `core/store/migrations.py` — migracion `_V2` (aditiva, con backfill de filas viejas).
- `interno/ui/main_window.py` — una tabla de columnas por item en la pestana Solicitudes.
