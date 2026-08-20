# 016 — La coordinacion se puede abrir sobre el SQLite espejo

**Tags:** `#bd` · `#arquitectura` · `#ui-interno` · `#ui-aliado` ·
**Fecha:** 2026-08-20 · **Extiende:** [009](009-respaldo-sqlite-de-las-tablas-guispk.md)

## Contexto

El DSN corporativo presenta intermitencias. Con la coordinacion viviendo solo en
Impala (006), una caida deja las dos apps inutiles: no se puede consultar el estado
de una solicitud, ni registrar una nueva, ni revisar el historico. El trabajo se
detiene aunque no tenga nada que ver con ejecutar enmascaramiento.

Ya existia el archivo que hacia falta: el respaldo de 009 es una copia fiel de las
cinco tablas, con las mismas columnas, generadas de `ddl.SCHEMA`. Lo que faltaba era
poder **trabajar** sobre el, no solo restaurarlo.

## Decision

Un switch de backend en las dos apps: Impala (como siempre) o un archivo SQLite.

La pieza es `core/store/sqlite_runner.py`, que cumple el mismo `ImpalaRunner`
(Protocol de `core/store/runner.py`) que los repos ya esperaban. Los repos no
cambian una linea: su SQL es ANSI con `?`, que SQLite ejecuta nativo.

**Es el mismo archivo del respaldo**, no un formato nuevo. Las tablas se crean
planas (`guispk_*`, sin esquema) igual que en `backup.py`, y el nombre calificado
`proceso_enmascarado.guispk_*` que generan los repos se resuelve con
`ATTACH <archivo> AS proceso_enmascarado`. Un `.db` de respaldo se abre tal cual.

El camino de vuelta ya estaba escrito: cuando el DSN se recupera, el `restore` de
009 sube lo que se escribio — inserta por id lo que falta, sin borrar ni actualizar,
asi que correrlo dos veces no duplica eventos.

## Lo que no funciona en modo SQLite

Solo se muda la **coordinacion**. Las tablas de negocio siguen en Impala, asi que
`DESCRIBE`, `SHOW PARTITIONS`, la vista previa (015) y la ejecucion de scripts no
tienen contra que correr. La UI lo dice y desactiva lo que no aplica:

- Ejecutar una solicitud exige `client.connected`, no solo que el store responda.
- Refrescar el catalogo y la vista previa ya lo exigian.
- Decidir el enmascaramiento, rechazar, consultar y registrar solicitudes **si**
  funcionan: son escrituras de eventos.

Respaldar o restaurar hacia el archivo que esta abierto como coordinacion se
bloquea: `backup` hace DELETE + INSERT por tabla y se llevaria por delante justo lo
que se quiere guardar.

## Detalles que importan

- `STRING` → `TEXT` en el DDL (no en los INSERT): a un tipo que no reconoce, SQLite
  le da afinidad NUMERIC y guardaria como float un id que resulte ser todo digitos.
  Despues el `sorted` del fold compara float con str y revienta. Es la misma razon
  que ya tenia `tests/fake_impala.py`.
- `journal_mode=DELETE` y `synchronous=FULL`: el archivo vive en OneDrive/SMB, donde
  WAL no es confiable (igual que en `backup.py`).
- `check_same_thread=False` + `Lock`: los repos se llaman desde QThreads
  (`core/ui/repo_worker.py`) sobre una conexion compartida.
- El aliado abre el archivo con `ensure_schema=False`: crear las `guispk_*` sigue
  siendo tarea de la app interna (006). En modo archivo no se le pide DSN ni
  contrasena, pero si el usuario: firma las solicitudes.

## Consecuencias

- Varios escritores sobre un archivo en OneDrive pueden dar conflictos de sync. Lo
  mitiga que todo sea append-only y que el restore deduplique por id, pero no lo
  elimina: el modo archivo es para una caida, no para operar en paralelo.
- `resolve_settings` gana `backend` y `sqlite_path`. Sin ruta propia se ofrece la
  del respaldo, que es el mismo archivo.
- Un backend desconocido en la configuracion cae a Impala, no a un error.

## Archivos

- `core/store/sqlite_runner.py` — el runner.
- `core/config.py` — `backend` / `sqlite_path`, `BACKEND_IMPALA` / `BACKEND_SQLITE`.
- `interno/ui/main_window.py` — el switch en el tab Conexion, el guard de ejecutar
  y el de respaldar sobre el archivo en uso.
- `aliado/ui/connect_dialog.py` — la opcion de archivo en el dialogo de conexion.
- `main_interno.py`, `main_aliado.py` — pasan la configuracion.
- `tests/test_sqlite_runner.py` — repos sobre el archivo, apertura de un respaldo,
  vuelta a Impala con restore, persistencia, hilos y la precedencia de config.
