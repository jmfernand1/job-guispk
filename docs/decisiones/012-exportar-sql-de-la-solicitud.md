# 012 — Exportar el SQL de una solicitud a un .sql editable

**Tags:** `#sql` `#seguridad` `#ui-interno` · **Fecha:** 2026-08-12

## Contexto

La app arma el SQL de una solicitud y lo ejecuta en un solo paso: el interno elige las
mascaras y aprieta *Ejecutar*. Cuando una corrida necesita una variante que la app no
ofrece — cambiar el WHERE, insertar en dos partes, agregar un `SET` de sesion, correr solo
el CREATE para revisarlo con el DBA — no habia por donde sacar ese SQL: el unico botón
*Guardar .sql* vive en el Historico y solo aparece **despues** de haber ejecutado.

## Decision

Un boton **Exportar SQL (.sql)** en la pestana Solicitudes del interno, junto a
*Ejecutar* y *Rechazar*. Guarda en un archivo el SQL de todas las tablas de la solicitud,
armado con **las mascaras que estan en pantalla** (no con `fields_final`): exportar sirve
justamente para mirar el SQL antes de decidir.

El SQL exportado es **exactamente el que se ejecutaria**: el armado lo hace
`BuildScriptsWorker`, que repite lo que hace `ExecuteRequestWorker` antes de correr una
sola sentencia — valida la decision contra lo pedido (`review.validate_final_fields`) y
re-resuelve la particion del origen contra Impala — y ahi se detiene. La re-resolucion de
particion, que es la parte que consulta Impala, quedo en `resolve_partition`, compartida
por los dos workers para que no puedan divergir.

El archivo sale **con los salts reales sustituidos**, como ya hacia *Guardar script SQL*
del Ad-hoc: se escribe para ejecutarse a mano, y con `{{TEXT_SALT}}` no correria. La
regla de no escribir salts sigue intacta donde importa — la BD compartida: el script solo
se sustituye en memoria, al momento de escribir el archivo local que el interno eligio.

## Consecuencias

- **El archivo es secreto.** Lleva los salts: la cabecera del `.sql` lo avisa y el dialogo
  final lo repite. No va a un repositorio ni al aliado.
- **Lo que se corra desde el archivo no existe para la app**: no queda en el historico, no
  cambia el estado de la solicitud y no re-resuelve nada. La cabecera tambien lo dice.
- Exportar es **solo lectura**: no persiste la decision ni transiciona la solicitud, asi
  que el boton queda habilitado tambien para solicitudes ya ejecutadas o rechazadas, que
  cargan sus columnas en modo consulta.
- **Sin conexion se puede exportar igual**, avisando: sin Impala no hay particion fresca,
  el script sale con el WHERE de la solicitud y el destino sin `PARTITIONED BY`. Es la
  misma caida que ya tenia la ejecucion cuando `get_partition_info` falla.
- Armar el SQL pasa por un `QThread` como todo lo que toca Impala: re-resolver la
  particion tarda segundos y no puede correr en el hilo de UI.

## Archivos

- `core/sql_builder.py` — `build_export_script`: une los scripts de la solicitud con una
  cabecera de comentarios.
- `interno/workers.py` — `resolve_partition` (extraida de `ExecuteRequestWorker`) y
  `BuildScriptsWorker`.
- `interno/ui/main_window.py` — boton `export_sql_btn` y `on_export_request_sql` /
  `_on_request_sql_built`.
- `tests/test_export_sql.py` — que el .sql exportado sea identico al que se ejecutaria,
  que el script guardado conserve los placeholders y que el archivo salga sin ellos.
