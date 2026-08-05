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

## Tags

- `#arquitectura` — separacion de capas y de apps: 001
- `#bd` — esquema y uso del SQLite compartido: 001, 005
- `#estados` — ciclo de vida de una solicitud: 002
- `#enmascaramiento` — quien decide que se enmascara: 003
- `#historico` — registro y re-ejecucion de scripts: 005
- `#seguridad` — salts, secretos y limites de confianza: 001, 003, 005
- `#sql` — forma del SQL generado: 004
- `#ui-aliado` — pantallas del aliado: 003
- `#ui-interno` — pantallas del equipo interno: 002, 003, 005

## Como agregar una decision

1. Crea `docs/decisiones/NNN-slug.md` copiando la estructura de cualquier entrada
   existente: **Contexto → Decision → Consecuencias → Archivos**.
2. Agrega la fila al indice de arriba y suma el numero a los tags que apliquen.
3. Si la decision cambia como funciona el sistema hoy, actualiza tambien el README:
   este archivo guarda el *por que*, el README el *que*.
