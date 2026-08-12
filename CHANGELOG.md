# Decisiones del proyecto

Indice de las decisiones de diseno y por que se tomaron. **Esta pagina es solo el
indice**: cada entrada resume la decision en una linea; el detalle (contexto,
consecuencias y archivos tocados) vive en su propio archivo bajo
[`docs/decisiones/`](docs/decisiones/). Lee el indice y abre unicamente la entrada
que te interese.

Para el estado **actual** del sistema — como funciona hoy, no por que — ve al
[README](README.md).

## Indice

| # | Decision | Tags | Fecha |
|---|----------|------|-------|
| [001](docs/decisiones/001-dos-apps-bd-compartida.md) | Una sola app se parte en **interno** y **aliado** sobre un SQLite compartido, sin servidor. | `#bd` `#seguridad` `#arquitectura` | 2026-08-04 |
| [002](docs/decisiones/002-sin-paso-de-aprobacion.md) | Se elimina el estado `aprobada`: el interno **ejecuta o rechaza** directo desde `enviada`. | `#estados` `#ui-interno` | 2026-08-05 |
| [003](docs/decisiones/003-enmascaramiento-lo-decide-el-interno.md) | El aliado pide **columnas**; el interno decide la **mascara** de cada una y puede excluirlas. | `#enmascaramiento` `#seguridad` `#ui-aliado` `#ui-interno` | 2026-08-05 |
| [004](docs/decisiones/004-drop-purge-antes-de-crear.md) | Cada ejecucion arranca con `DROP TABLE IF EXISTS <destino> PURGE`: la corrida **reemplaza**, ya no acumula. | `#sql` | 2026-08-05 |
| [005](docs/decisiones/005-historico-re-ejecutable.md) | Todo script ejecutado queda en `script_history` y se puede **re-ejecutar** desde la app interna. | `#historico` `#seguridad` `#bd` `#ui-interno` | 2026-08-05 |
| [006](docs/decisiones/006-coordinacion-impala-append-only.md) | La coordinacion se muda del SQLite/OneDrive a tablas `guispk_*` **append-only en Impala** (`proceso_enmascarado`); supersede parcialmente 001. | `#bd` `#arquitectura` `#seguridad` | 2026-08-05 |
| [007](docs/decisiones/007-columnas-sin-palabras-reservadas.md) | Ninguna columna se llama como palabra reservada: `at`/`who`/`role`/`comment` pasan a `event_at`/`event_by`/`actor_role`/`note`. | `#bd` `#sql` | 2026-08-06 |
| [008](docs/decisiones/008-aliado-sparky-con-fallback-odbc.md) | El aliado se conecta por **Sparky** y cae a **ODBC** si falla; el adaptador se muda a `core/`. | `#arquitectura` `#seguridad` `#ui-aliado` | 2026-08-06 |
| [009](docs/decisiones/009-respaldo-sqlite-de-las-tablas-guispk.md) | El interno respalda las `guispk_*` a un `.db` en OneDrive y **restaura** desde ahi si las borran; agendable con `tools/backup_guispk.py`. | `#bd` `#seguridad` `#ui-interno` | 2026-08-06 |
| [010](docs/decisiones/010-destino-particionado-como-el-origen.md) | La tabla destino se crea con `PARTITIONED BY` heredando las columnas de particion del origen. | `#sql` | 2026-08-07 |
| [011](docs/decisiones/011-buscador-de-columnas.md) | Buscador que filtra columnas mientras se escribe en las tres vistas de seleccion; "Seleccionar todo" pasa a actuar solo sobre las visibles. | `#ui-aliado` `#ui-interno` | 2026-08-11 |
| [012](docs/decisiones/012-exportar-sql-de-la-solicitud.md) | El interno puede **exportar a un .sql** el SQL de una solicitud (el mismo que se ejecutaria, con los salts reales) para editarlo y correrlo por fuera. | `#sql` `#seguridad` `#ui-interno` | 2026-08-12 |

## Tags

- `#arquitectura` — separacion de capas y de apps: 001, 006, 008
- `#bd` — esquema y uso de la BD de coordinacion: 001, 005, 006, 007, 009
- `#estados` — ciclo de vida de una solicitud: 002
- `#enmascaramiento` — quien decide que se enmascara: 003
- `#historico` — registro y re-ejecucion de scripts: 005
- `#seguridad` — salts, secretos y limites de confianza: 001, 003, 005, 006, 008, 009, 012
- `#sql` — forma del SQL generado: 004, 007, 010, 012
- `#ui-aliado` — pantallas del aliado: 003, 008, 011
- `#ui-interno` — pantallas del equipo interno: 002, 003, 005, 009, 011, 012

## Como agregar una decision

1. Crea `docs/decisiones/NNN-slug.md` copiando la estructura de cualquier entrada
   existente: **Contexto → Decision → Consecuencias → Archivos**.
2. Agrega la fila al indice de arriba y suma el numero a los tags que apliquen.
3. Si la decision cambia como funciona el sistema hoy, actualiza tambien el README:
   este archivo guarda el *por que*, el README el *que*.
