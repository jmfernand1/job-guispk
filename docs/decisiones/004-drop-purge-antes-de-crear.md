# 004 — `DROP TABLE ... PURGE` antes de crear

**Tags:** `#sql` · **Fecha:** 2026-08-05 · **Commit:** `7318fbf`

## Contexto

El script generado era `CREATE TABLE IF NOT EXISTS` + `INSERT INTO`. Cuando la tabla
destino ya existia y habia que **recrearla** — porque cambiaron las columnas, la mascara o
simplemente porque se volvia a correr — el `CREATE` no hacia nada y el `INSERT` se
acumulaba sobre los datos anteriores. El resultado era una tabla con filas mezcladas de
varias corridas, o un fallo de esquema si las columnas ya no coincidian.

## Decision

Todo script generado arranca con:

```sql
DROP TABLE IF EXISTS <destino> PURGE;
```

`build_script` pasa a devolver **cuatro** valores — `(drop, create, insert, script)` — y las
tres sentencias van al script completo en ese orden. Aplica a los tres caminos: ejecucion
de solicitud, Ad-hoc y re-ejecucion desde el historico.

`PURGE` salta la papelera de HDFS: la tabla anterior se borra de inmediato en vez de quedar
ocupando espacio en `.Trash`.

## Consecuencias

- **Cambia la semantica de la ejecucion.** Antes el `INSERT INTO` acumulaba sobre el
  destino existente; ahora cada corrida **reemplaza**: la tabla destino queda con
  exactamente el resultado de la ultima ejecucion. Si alguna vez se necesita acumular,
  hay que volver esto opcional.
- Es un borrado real e irreversible sobre el cluster. Los dialogos de confirmacion — el de
  solicitudes, el de Ad-hoc y el de re-ejecucion — lo advierten con todas las letras antes
  de correr nada.
- Hace que re-ejecutar un script del historico ([005](005-historico-re-ejecutable.md)) sea
  seguro de repetir: no falla porque el destino ya exista.
- `CREATE TABLE IF NOT EXISTS` se conservo aunque con el DROP delante sea redundante:
  `build_create` se usa tambien por separado.

## Archivos

- `core/sql_builder.py` — `build_drop`, `build_script`.
- `interno/workers.py` — `ExecuteRequestWorker`, `RerunScriptWorker`.
- `interno/ui/adhoc_panel.py` y `interno/ui/main_window.py` — dialogos de confirmacion.
