# 013 — El INSERT escribe la particion con valores, no dinamica

**Tags:** `#sql` · **Fecha:** 2026-08-12 · **Corrige:** [010](010-destino-particionado-como-el-origen.md)

## Contexto

La regla 3 de la decision 010 dejaba el INSERT en forma **dinamica**: las columnas
de particion iban al final del SELECT y la clausula era `PARTITION (year, month, day)`
con los nombres pelados. Impala tenia que deducir la particion fila por fila.

Es innecesario y confuso: la corrida siempre es de **una sola particion** — el WHERE
que filtra el origen es exactamente `year = 2026 and month = 8 and day = 10`, la
ultima particion que devuelve `SHOW PARTITIONS`. El destino que se escribe se conoce
antes de correr nada.

## Decision

El INSERT escribe la particion a mano, con los valores tomados del mismo WHERE:

```sql
INSERT INTO destino PARTITION (year = 2026, month = 8, day = 10)
SELECT
  mask_text(nombre, '...') AS nombre,
  saldo AS saldo
FROM origen
WHERE year = 2026 and month = 8 and day = 10;
```

Dos cambios respecto de 010:

1. La clausula lleva `col = valor` separados por comas, en el orden de particion del
   origen.
2. **Las columnas de particion salen del SELECT.** En un insert estatico Impala no
   las espera ahi: si se dejan, sobran contra la lista de columnas del destino.

Los valores se leen del WHERE (`sql_builder.partition_values`) y se copian **tal
cual**, sin citarlos ni reinterpretarlos: es el mismo texto con el que se filtra el
origen, asi que se escribe la particion que se lee. El WHERE se mantiene: es lo que
limita el SELECT.

## Caidas al insert dinamico

Se conserva la forma dinamica de 010 para los dos casos en que no se puede escribir
el valor:

- **El WHERE no da valor para alguna columna de particion** (viene vacio, o el
  termino no es una igualdad simple). Sin valor no hay particion estatica posible.
- **La columna de particion va enmascarada.** El valor estatico se copia del origen
  sin pasar por la mascara; para que la mascara se aplique tiene que escribirla el
  SELECT. Enmascarar la clave de particion sigue siendo decision del interno, y esta
  caida es lo que la hace real.

## Consecuencias

- Se escribe una sola particion por corrida, explicita y visible en el script: lo
  que se va a tocar se lee en la primera linea del INSERT.
- `partition_cols=None` sigue dando el SQL de siempre — sin particion no hay
  clausula que cambiar — asi que la verificacion de solicitudes viejas por
  regeneracion no se mueve.
- Los scripts ya guardados en el historico se re-ejecutan tal cual, con la forma
  dinamica con la que se generaron.

## Archivos

- `core/sql_builder.py` — `partition_values` y la rama estatica de `build_insert`.
- `tests/test_partitioning.py` — la forma estatica, la salida del SELECT y las dos
  caidas.
