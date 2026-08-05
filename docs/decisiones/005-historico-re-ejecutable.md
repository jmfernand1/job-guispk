# 005 — Historico de scripts re-ejecutable

**Tags:** `#historico` `#seguridad` `#bd` `#ui-interno` · **Fecha:** 2026-08-05 · **Commit:** `7318fbf`

## Contexto

`requests.execution_log` guardaba un resumen en texto de cada ejecucion, pero **no el SQL
que efectivamente corrio**. Si habia que volver a sacar una tabla — porque se recargo la
fuente, porque fallo a mitad, o simplemente para repetir lo mismo del mes pasado — el
interno tenia que rearmar la solicitud desde cero. Tampoco habia forma de responder "que
se ejecuto exactamente sobre esta tabla y cuando".

## Decision

Una tabla `script_history` (migracion `_V3`) que registra **toda** ejecucion, con su origen:

| origen | de donde viene |
|---|---|
| `solicitud` | ejecucion de una solicitud del aliado |
| `adhoc` | pestana Ad-hoc del interno |
| `re-ejecucion` | se volvio a correr un script del historico |

Guarda fecha, quien, tablas origen/destino, WHERE usado, etiqueta del salt, el script y el
resultado (`ok` / `error`, con el mensaje si fallo). Los fallos tambien se registran.

En la app interna, la pestana **Historico** lista todo con un buscador por tabla, codigo de
solicitud o usuario; permite ver el script, guardarlo como `.sql` y **re-ejecutarlo**.

## Consecuencias

- **El script se guarda con los placeholders `{{TEXT_SALT}}` / `{{INT_SALT}}`, nunca con
  los salts reales.** La BD es compartida y los aliados la leen (ver
  [001](001-dos-apps-bd-compartida.md)); guardar el SQL ya sustituido filtraria el secreto.
  Al re-ejecutar se vuelven a sustituir con los salts locales. Hay un test que lo bloquea.
- La re-ejecucion corre el script **tal cual**: no re-resuelve la particion, usa el WHERE
  guardado. Es lo que se quiere al repetir una corrida concreta.
- Si el salt actual no es el de la corrida original, se avisa antes de ejecutar: los datos
  enmascarados no coincidirian con los de la primera vez y se rompe la integridad
  referencial entre tablas.
- Depende de [004](004-drop-purge-antes-de-crear.md): sin el `DROP` inicial, re-ejecutar
  fallaria o acumularia.
- El corte del script en sentencias (`split_statements`) se hace **antes** de sustituir los
  salts, asi un salt que contenga `;` no puede partir una sentencia.

## Archivos

- `core/store/history_repo.py` — `record`, `list_scripts`, `get`.
- `core/store/migrations.py` — migracion `_V3`.
- `core/sql_builder.py` — `split_statements`.
- `interno/workers.py` — `RerunScriptWorker` y el registro en `ExecuteRequestWorker`.
- `interno/ui/main_window.py` — pestana Historico.
- `tests/test_history.py` — incluye el test de que no se filtren salts.
