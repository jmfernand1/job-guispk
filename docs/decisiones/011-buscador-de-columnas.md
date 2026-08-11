# 011 — Buscador de columnas en las vistas de seleccion

**Tags:** `#ui-aliado` `#ui-interno` · **Fecha:** 2026-08-11

## Contexto

Las tablas del origen traen habitualmente cientos de columnas. La `ColumnTable` las lista
todas y la unica forma de encontrar una era bajar con la rueda del raton leyendo nombres,
en las tres pantallas donde se seleccionan campos: la solicitud del aliado, la revision de
la solicitud en el interno y el Ad-hoc.

## Decision

Una barra de busqueda (`ColumnFilterBar`) sobre cada `ColumnTable` que **filtra mientras se
escribe** ocultando las filas que no coinciden. La coincidencia (`matches`) normaliza
acentos y mayusculas y acepta tres formas, de la mas estricta a la mas laxa:

1. subcadena — `cli` → `num_cliente`;
2. todas las palabras sueltas, en cualquier orden — `cliente num` → `num_cliente`;
3. subsecuencia — `nclte` → `num_cliente`, para nombres que se recuerdan a medias.

El filtro es **solo vista**: las filas ocultas conservan su marca, asi que
`selected_fields` / `selected_columns` devuelven lo mismo con o sin filtro puesto. Escribir
en la caja nunca puede cambiar lo que se solicita ni lo que se ejecuta.

## Consecuencias

- **`set_all_checked` pasa a actuar solo sobre las filas visibles.** Con el filtro vacio se
  comporta igual que antes; con filtro, "Seleccionar todo" marca lo que se esta viendo, que
  es lo que el usuario espera al haber escrito un nombre. Es el unico cambio de conducta.
- Por eso la barra muestra un contador (`12 de 340`): con el filtro puesto hay que ver
  sobre cuantas filas van a actuar esos botones.
- En la revision del interno la barra queda **activa aunque la tabla este deshabilitada**
  (solicitudes ya ejecutadas o rechazadas): filtrar no edita nada y ayuda a revisar.
- Recargar columnas (`load_columns` / `load_fields`) reaplica el filtro vigente en vez de
  dejar filas ocultas de la carga anterior.

## Archivos

- `core/ui/column_table.py` — `normalize`, `matches`, `ColumnTable.set_filter`,
  senal `filterChanged`, `ColumnFilterBar`.
- `aliado/ui/main_window.py`, `interno/ui/adhoc_panel.py`, `interno/ui/main_window.py` —
  la barra sobre cada tabla (en el interno, dentro de la pestana de cada item).
- `tests/test_column_filter.py` — que encuentra y que no la funcion de coincidencia.
