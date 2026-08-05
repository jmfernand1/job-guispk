# 001 — Dos apps sobre un SQLite compartido

**Tags:** `#bd` `#seguridad` `#arquitectura` · **Fecha:** 2026-08-04 · **Commit:** `5ea4712`

## Contexto

El motor arranco como una app unica que un interno usaba de punta a punta: DESCRIBE,
elegir mascaras, ejecutar. Cuando entraron los aliados al proceso, esa app no servia:
los aliados no tienen acceso a las zonas internas ni pueden conectarse a Impala, pero
si necesitan pedir tablas enmascaradas y saber en que va su pedido.

Montar un servicio (API + base de datos) no era viable en este entorno: no hay donde
desplegarlo ni quien lo opere.

## Decision

Dos ejecutables sobre un archivo **SQLite en carpeta compartida** (OneDrive / unidad de
red), sin servidor en el medio:

- `interno/` — conexion a Sparky/Impala, inventario, catalogo, ejecucion.
- `aliado/` — 100% offline contra la BD compartida. **Nunca importa `interno/` ni
  `sparky_bc`**, y su ejecutable se construye excluyendolos (`guispk_aliado.spec`).
- `core/` — lo comun: masking, sql_builder, states, store, widgets.

La BD compartida **no contiene secretos**: los aliados leen ese archivo, asi que ni
credenciales ni salts se guardan ahi. Los salts reales viven solo en las maquinas
internas (`~/.guispk/salts.json`) y el SQL se guarda con placeholders
`{{TEXT_SALT}}` / `{{INT_SALT}}`.

## Consecuencias

- La concurrencia se resuelve con **bloqueo optimista**, no con transacciones largas:
  cada transicion hace `UPDATE ... WHERE id = ? AND state = <estado leido>` y falla
  limpio si otro proceso se adelanto.
- `journal_mode=DELETE` en vez de WAL: WAL no es confiable en carpetas de red. Con 6
  usuarios y escrituras esporadicas alcanza.
- Cualquier cambio de esquema debe ser **aditivo**: puede haber un .exe viejo escribiendo
  en la misma BD. Las migraciones ya publicadas no se editan (ver `core/store/migrations.py`).
- La separacion de capas es una regla de seguridad, no de estilo: si `aliado/` importara
  `interno/`, el ejecutable del aliado terminaria con codigo de conexion adentro.

## Archivos

- `core/store/db.py`, `core/store/migrations.py` — conexion y esquema.
- `core/config.py` — resolucion de la ruta de la BD (`GUISPK_DB`, `config.ini`, fallback).
- `guispk_aliado.spec` — `excludes=['sparky_bc', 'interno', 'tests']`.
