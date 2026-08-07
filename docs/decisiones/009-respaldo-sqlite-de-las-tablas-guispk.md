# 009 — Respaldo de las `guispk_*` a un SQLite en OneDrive

**Fecha:** 2026-08-06
**Tags:** `#bd` `#seguridad` `#ui-interno`

## Contexto

La coordinacion entera —inventario, catalogo, solicitudes con su auditoria e
historico de scripts— vive en cinco tablas de `proceso_enmascarado`, un esquema
que el equipo interno **no administra**. Una limpieza de zona o un `DROP` ajeno
se lo lleva todo, y no hay de donde sacarlo: el SQLite de
[006](006-coordinacion-impala-append-only.md) quedo archivado como solo-lectura
en el corte y no refleja nada posterior.

Los aliados perdieron la sincronizacion de OneDrive, pero **el equipo interno
no**: sigue siendo un lugar donde ellos pueden dejar un archivo.

## Decision

`core/store/backup.py`, que corre **solo la app interna** (pestana Respaldo):

- **`backup(runner, db_path, schema)`** vuelca las cinco tablas a un `.db`
  SQLite. Es una **foto**: reemplaza cada tabla entera (DELETE + INSERT en una
  transaccion por tabla), no acumula versiones.
- **`restore(runner, db_path, schema)`** reinserta en Impala las filas cuyo id
  no esta ya ahi. **No borra ni actualiza**: las `guispk_*` son append-only y
  el restore no es la excepcion.
- **`summary(db_path)`** lista que hay en un respaldo sin tocar Impala.

El SQLite es una **copia fiel**, no un formato propio: mismas tablas, mismas
columnas, generadas desde `ddl.SCHEMA`. Restaurar es reinsertar tal cual, sin
conversiones que se desalineen con el tiempo.

Para tener una sola fuente de verdad, `core/store/ddl.py` pasa de emitir texto
SQL a mano a declarar `SCHEMA` como datos (tabla → columnas + columna id) y
generar el `CREATE TABLE` desde ahi. El espejo SQLite y el test de palabras
reservadas ([007](007-columnas-sin-palabras-reservadas.md)) leen la misma
estructura.

### Por que el restore deduplica por id

Un evento repetido no es inocuo: el estado de una solicitud es el *fold* de sus
eventos, y una transicion duplicada es una transicion mas. Deduplicar por
`event_id` / `capture_id` / `item_id` / `script_id` hace que el restore se
pueda correr sobre tablas vacias (el desastre), sobre un restore que se corto a
la mitad, o dos veces seguidas, siempre con el mismo resultado.

### Sin secretos, como la fuente

El `.db` va a OneDrive, asi que aplica la regla de siempre: los scripts se
copian con `{{TEXT_SALT}}` / `{{INT_SALT}}` porque asi estan guardados en
Impala. `tests/test_backup.py` verifica que el script sale del respaldo
identico a como entro — si alguien hiciera que el respaldo resolviera los
salts, ese test falla.

## Consecuencias

- Ademas de la pestana, hay un entry point sin UI para agendar:
  `python -m tools.backup_guispk` (ver *Agendado* abajo). La pestana sirve para
  el respaldo puntual antes de un cambio y para el restore; el agendado es el
  que cubre el olvido.
- El restore **necesita las tablas creadas**. Tras un `DROP`, reconectar la app
  interna las recrea (`ensure_remote_schema`) y recien ahi se restaura.
- Un `.db` de respaldo desactualizado que se restaure sobre tablas vivas no
  rompe nada: lo que ya esta se omite, y lo viejo que reaparezca es historia
  que el fold ya sabe ignorar si no encaja.

## Agendado

`tools/backup_guispk.py` hace lo mismo que la pestana, sin UI:

```
python -m tools.backup_guispk              # respalda (ruta del config.ini)
python -m tools.backup_guispk --restore    # recrea el esquema y repuebla
python -m tools.backup_guispk --summary    # que hay en el .db, sin tocar Impala
```

Las credenciales salen de `USERNAME` / `PSWD` / `DSNLZ` y **no se aceptan por
argumento**: quedarian en el historial de la shell y en la definicion de la
tarea agendada. Termina con codigo 0 o 1, que es lo unico que mira el
agendador para avisar de un fallo.

`--restore` corre `ensure_remote_schema` antes de restaurar, asi que cubre el
caso feo entero: borraron las tablas, se recrean vacias y se repueblan en un
solo comando.

## Nota al margen: bug encontrado en `tests/fake_impala.py`

Los tests nuevos destaparon una falla vieja e intermitente del runner falso.
SQLite le da afinidad **NUMERIC** a un tipo que no reconoce, y `STRING` no lo
reconoce: un id que resultara ser todo digitos (~1 de cada 350, `new_id` =
timestamp + 12 hex al azar) se guardaba como **float** y el `sorted` del fold
reventaba comparando float con str. El DDL del fake ahora traduce
`STRING` → `TEXT`. Solo afectaba a los tests; Impala nunca tuvo el problema.

## Archivos

`core/store/{backup,ddl}.py`, `core/config.py`, `config.ini.example`,
`interno/ui/main_window.py`, `main_interno.py`, `tools/backup_guispk.py`,
`tests/{test_backup,test_backup_cli,fake_impala}.py`.
