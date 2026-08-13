# 010 — La tabla destino se particiona igual que el origen

**Fecha:** 2026-08-07
**Tags:** `#sql`

## Contexto

El CREATE generado creaba siempre una tabla plana. Las tablas origen de la
zona estan particionadas (tipicamente por fecha de ingestion), asi que el
destino enmascarado perdia la particion: cada consulta sobre el destino
terminaba en full scan, y no habia forma de reemplazar una particion sin
reescribir la tabla entera.

## Decision

`build_create` y `build_insert` reciben `partition_cols` — los nombres de las
columnas por las que particiona el **origen**, tal como los devuelve
`SparkyClient.get_partition_info`. El destino se crea con la misma clave.

Tres reglas que impone Impala y que el generador respeta:

1. **Las columnas de particion no se repiten** en la lista de columnas
   normales del CREATE. Repetirlas es `Duplicate column name`.
2. **Llevan tipo**, no solo el nombre: `PARTITIONED BY (ingestion_year int)`.
   La forma con nombres pelados existe solo en el `CREATE TABLE ... AS SELECT`,
   y aqui el CREATE es con lista de columnas explicita.
3. **En el INSERT van al final del SELECT**, con clausula
   `PARTITION (col, ...)` explicita: es un insert dinamico, y Impala exige ese
   orden. *(Corregido por [013](013-insert-con-particion-estatica.md): hoy el
   INSERT escribe los valores — `PARTITION (year = 2026, ...)` — y esas columnas
   salen del SELECT; el insert dinamico quedo como caida.)*

Se particiona solo por las columnas de particion que el interno dejo salir: una
columna excluida de la solicitud no puede ser clave del destino. Si no queda
ninguna, el destino sale plano, como antes.

Las columnas de particion se leen del **origen al momento de ejecutar**, no del
snapshot de la captura: si la tabla origen cambio su particionado, manda el
origen. Cuando no se puede leer, el destino queda sin particionar y el log de
la solicitud lo dice.

## Compatibilidad

`partition_cols=None` da exactamente el SQL de antes, y ese default es
deliberado: la verificacion de solicitudes en formato viejo
(`interno/workers.py`) regenera su script y lo compara contra el guardado. Si
la regeneracion agregara particion, toda solicitud vieja fallaria la
verificacion y no se podria ejecutar. Hay un test que fija esa equivalencia.

Los scripts ya guardados en el historico se re-ejecutan tal cual, sin
particion: son un registro de lo que corrio, no de lo que correria hoy.

## Consecuencias

- El destino hereda el particionado, con lo que eso implica: si la particion
  tiene mucha cardinalidad, el INSERT dinamico genera muchos archivos chicos.
  Las corridas son por particion (`WHERE` de la ultima), asi que en la practica
  se escribe una sola.
- Enmascarar una columna de particion sigue siendo posible y sigue siendo
  decision del interno; el generador no opina, solo la coloca donde va.

## Archivos

`core/sql_builder.py`, `interno/workers.py`, `interno/ui/adhoc_panel.py`,
`tests/test_partitioning.py`.
