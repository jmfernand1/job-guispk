# 008 — El aliado se conecta por Sparky y cae a ODBC

**Fecha:** 2026-08-06
**Tags:** `#arquitectura` `#seguridad` `#ui-aliado`
**Modifica:** [006](006-coordinacion-impala-append-only.md) — el aliado ya no
es exclusivamente pyodbc.

## Contexto

006 asumio que el aliado solo tenia DSN ODBC. En la practica parte de los
aliados **si tiene `sparky_bc`** disponible, con las mismas credenciales que
usa el equipo interno. Obligarlos a ODBC agrega una configuracion de DSN que a
veces no esta puesta, y deja dos caminos distintos hacia el mismo cluster segun
quien ejecute la app.

## Decision

`aliado/impala_client.py:connect_impala(dsn, username, password)` intenta
**Sparky primero** y cae a **ODBC** ante cualquier fallo (libreria ausente,
login rechazado, red). Devuelve `(runner, backend, fallback_error)`; el backend
se muestra en la barra de estado del aliado y `fallback_error` conserva el
motivo, para que "quede en ODBC" nunca sea silencioso.

Los dos imports son **perezosos**: un aliado sin `sparky_bc` (o sin `pyodbc`)
no falla al arrancar, solo usa el otro camino.

Para que el aliado pueda usar Sparky sin romper la separacion de capas, el
adaptador se muda de `interno/` a `core/`:

- `interno/sparky_client.py` → `core/sparky_client.py`
- `interno/sparky_runner.py` → `core/sparky_runner.py`

**La regla de capas se mantiene con un matiz:** `aliado/` sigue sin importar
`interno/` (y el `.spec` lo sigue excluyendo). Lo que cambia es que `sparky_bc`
deja de ser exclusivo del interno: era un limite de *disponibilidad*, no de
*confianza*. El limite de confianza real no se toca — el enmascaramiento lo
decide el interno ([003](003-enmascaramiento-lo-decide-el-interno.md)), los
salts nunca salen de `~/.guispk/salts.json` del interno, y el aliado sigue
operando con GRANT SELECT + INSERT sobre las `guispk_*`: llegar por Sparky no
le da un permiso que ODBC no le diera.

`guispk_aliado.spec` deja de excluir `sparky_bc`. Si el entorno de build no lo
tiene, PyInstaller lo omite y el `.exe` sale solo-ODBC — el import perezoso y
el fallback lo cubren en runtime.

## Consecuencias

- El aliado necesita `dsn` aunque conecte por Sparky: es el mismo campo del
  dialogo y Sparky tambien lo usa.
- El ejecutable del aliado crece si se construye con `sparky_bc` en el entorno.
- Hay dos rutas de conexion que mantener; `tests/test_aliado_connect.py` cubre
  las cuatro combinaciones (Sparky ok, Sparky falla, sin libreria, los dos
  fallan).

## Alternativas descartadas

- **Elegir el backend con un flag de config**: mueve el problema al despliegue
  (alguien tiene que saber que aliado tiene que cosa) y falla igual de feo
  cuando la eleccion esta mal.
- **Dejar el adaptador en `interno/` e importarlo desde `aliado/`**: rompe la
  regla de capas de verdad y deja el `.spec` sin poder excluir `interno`.

## Archivos

`core/{sparky_client,sparky_runner}.py` (movidos desde `interno/`),
`aliado/impala_client.py`, `aliado/ui/{connect_dialog,main_window}.py`,
`main_aliado.py`, `guispk_aliado.spec`,
`interno/{workers,ui/main_window,ui/adhoc_panel}.py` (solo imports),
`tools/migrate_sqlite_to_impala.py` (solo imports),
`tests/test_aliado_connect.py`.
