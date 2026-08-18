# 006 — La coordinacion se muda a Impala (append-only)

**Fecha:** 2026-08-05
**Tags:** `#bd` `#arquitectura` `#seguridad`
**Supersede parcialmente:** [001](001-dos-apps-bd-compartida.md) — muere el
SQLite en carpeta compartida; sobreviven la separacion interno/aliado y la
regla "sin secretos en el canal compartido".

## Contexto

Los aliados perdieron la posibilidad de sincronizar o descargar la carpeta de
OneDrive: solo pueden trabajar en la web y **cargar** archivos. Un SQLite que
nadie puede abrir localmente deja de servir como canal entre las dos apps.

Lo que si cambio a favor: los aliados ahora tienen **DSN ODBC con credenciales
propias de Impala** y pueden llegar a la zona `proceso_enmascarado` — la misma
donde ya se escriben las tablas enmascaradas que consumen. La coordinacion se
muda ahi: tablas `guispk_*` en `proceso_enmascarado`, y desaparece la
dependencia de OneDrive.

## Decision

- **Tablas** (`core/store/ddl.py`): `guispk_inventory_events`,
  `guispk_schema_captures`, `guispk_request_items`, `guispk_request_events`,
  `guispk_script_history`. Todas Parquet e **insert-only**.
- **Append-only en vez de UPDATE**: Impala/Parquet no soporta UPDATE de filas,
  asi que el bloqueo optimista (`UPDATE ... WHERE state=?` + rowcount) se
  reemplaza por *event sourcing*: el estado de una solicitud es el **fold** de
  sus eventos en orden `(at, event_id)`. El fold ignora eventos cuyo
  `from_state` no coincide con el estado plegado; tras insertar, el repo
  re-pliega y si su evento quedo ignorado lanza `TransitionError` — el mismo
  contrato de concurrencia que antes. Los eventos son ademas la auditoria
  (`audit_log` desaparece).
- **IDs**: UUID ordenables por tiempo (`core/models.py:new_id`) en vez de
  rowids; el codigo de solicitud pasa de consecutivo `REQ-YYYYMMDD-NNN` a
  sufijo aleatorio `REQ-YYYYMMDD-XXXX` (sin constraints UNIQUE en Impala un
  consecutivo es una carrera permanente).
- **Conexiones**: el interno reutiliza su conexion Sparky
  (`interno/sparky_runner.py`); el aliado usa **pyodbc + su DSN**
  (`aliado/impala_client.py`) y sigue sin importar `sparky_bc` jamas. Solo el
  interno crea/asegura el esquema (`ensure_remote_schema`); los aliados operan
  con GRANT SELECT + INSERT sobre las `guispk_*`.
- **Latencia**: toda llamada a repos pasa por `core/ui/repo_worker.py`
  (QThread): contra Impala cada consulta tarda segundos y antes corria en el
  hilo de la UI.
- **Migracion**: `tools/migrate_sqlite_to_impala.py` replaya el `guispk.db`
  una unica vez (inventario, ultima captura por tabla, historico completo,
  solicitudes en estado final). Las pendientes no se migran: corte coordinado.
  El SQLite se archiva como solo-lectura.

## Lo que NO cambia

- El SQL se guarda con `{{TEXT_SALT}}` / `{{INT_SALT}}`; los salts reales solo
  viven en `~/.guispk/salts.json` del interno. Las tablas `guispk_*` las leen
  los aliados: siguen sin poder contener credenciales ni salts.
- El aliado pide columnas; el interno decide el enmascaramiento y solo puede
  restringir (`core/review.py`).
- La maquina de estados (`core/states.py`) es identica.

## Riesgos aceptados

- **Clock skew**: el desempate de eventos usa el reloj de cada maquina
  (`at` + prefijo temporal del `event_id`). Dos escrituras en el mismo segundo
  desde maquinas con relojes desviados pueden ordenarse "mal"; el fold lo
  resuelve de forma determinista y el perdedor recibe `TransitionError`.
- **Small files en Parquet**: cada INSERT de coordinacion crea un archivo
  pequeno. El volumen es minusculo; si algun dia molesta, un
  `INSERT OVERWRITE ... SELECT *` compacta.
- **El aliado ya no es offline**: necesita credenciales de Impala al arrancar
  (dialogo de conexion). Es el costo de perder OneDrive.
- **Sin Kudu**: soportaria UPDATE, pero agrega dependencia operativa y el
  patron append-only lo hace innecesario.

## Alternativas descartadas

- **Seguir en OneDrive via API web**: los aliados solo pueden cargar, no
  descargar; y programar contra la API de OneDrive requiere app registrada y
  tokens que nadie opera.
- **Consecutivo best-effort para el codigo**: COUNT + reintento sin UNIQUE
  colisiona entre maquinas y agrega una query de segundos por solicitud.

## Archivos

`core/store/{ddl,runner,requests_repo,catalog_repo,history_repo}.py`,
`core/{config,models}.py`, `core/ui/repo_worker.py`,
`interno/sparky_runner.py`, `interno/ui/{main_window,adhoc_panel}.py`,
`aliado/{impala_client}.py`, `aliado/ui/{main_window,connect_dialog}.py`,
`main_interno.py`, `main_aliado.py`, `tools/migrate_sqlite_to_impala.py`,
`tests/fake_impala.py`.
