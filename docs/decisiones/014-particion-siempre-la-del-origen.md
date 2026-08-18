# 014 — La particion del destino es siempre la del origen

**Tags:** `#sql` · **Fecha:** 2026-08-18 · **Corrige:** [010](010-destino-particionado-como-el-origen.md)

## Contexto

Probando la app aparecieron scripts sin `PARTITIONED BY` en el CREATE y sin
`PARTITION (...)` en el INSERT, contra tablas origen que si estan particionadas.

La causa: 010 decia particionar **solo por las columnas de particion que el interno
dejo salir**. Como el aliado pide columnas de negocio, casi nunca pide `year` /
`month` / `ingestion_day`: `split_partition_fields` no las encontraba entre los
campos, `part` quedaba vacio y todo el particionado desaparecia en silencio. El
destino salia plano y el INSERT sin clausula, justo en las tablas grandes.

Era una regla equivocada: una columna de particion **no es un dato**, es la clave
fisica de la tabla. No hace falta que el aliado la pida para que el destino se
particione — su valor ni siquiera sale del SELECT, lo escribe el `PARTITION` del
INSERT a partir del WHERE.

## Decision

Manda `SHOW PARTITIONS`. **Todas** las columnas que devuelve
`SparkyClient.get_partition_info` particionan el destino, esten o no entre los
campos seleccionados. La que no este se sintetiza como campo sin mascara y sale del
CREATE y del INSERT como cualquier otra clave de particion.

El tipo del `PARTITIONED BY` (Impala lo exige) se resuelve en este orden:

1. El tipo del campo, si la columna venia entre los seleccionados.
2. `partition_types`, el `{columna: tipo}` del `DESCRIBE` del origen — lo pasa el
   panel ad-hoc desde el DESCRIBE que ya tiene en pantalla, y `resolve_partition`
   describiendo el origen al ejecutar o exportar.
3. El valor del WHERE (`infer_partition_type`): entero sin comillas → `BIGINT`,
   cualquier otra cosa → `STRING`. Es el ultimo recurso, para que un DESCRIBE que
   falla no vuelva a dejar la tabla sin particionar.

Lo demas de 013 no cambia: el INSERT sigue siendo estatico con los valores del
WHERE, y se mantienen las dos caidas al insert dinamico (sin valor en el WHERE, o
columna de particion enmascarada).

## El WHERE y la particion no se separan

Los dos salen de `SHOW PARTITIONS`, asi que no puede haber una corrida que filtre
por particion sin escribirla en el destino. `resolve_partition` lo garantiza:

- **Pregunta al origen siempre**, traiga o no WHERE la solicitud. El snapshot del
  catalogo pudo capturarse con la tabla sin particionar o con el SHOW PARTITIONS
  caido (`CatalogRefreshWorker` lo tolera), y eso llegaba a la ejecucion como
  "sin particion": destino plano e INSERT de la tabla entera. Ahora se usa la
  ultima particion del origen y el log lo dice.
- **Si la consulta falla y la solicitud si traia WHERE**, las columnas se deducen
  de ese WHERE (`sql_builder.partition_cols_from_filters`) — tambien salio de
  SHOW PARTITIONS, sus columnas son las de particion. Antes ese camino dejaba el
  destino plano mientras el INSERT seguia filtrando.
- **Sin ninguna de las dos**, la tabla es plana de verdad y el SQL es el de siempre.

El builder no deduce columnas del WHERE por su cuenta: un WHERE escrito a mano no
particiona nada, y las solicitudes viejas tienen que regenerar identicas. La
deduccion vive en el worker, que sabe de donde vino ese WHERE.

En el panel ad-hoc no se puede preguntar dos veces (el interno ve el SQL antes de
ejecutarlo), pero un `SHOW PARTITIONS` que falla ya no es silencioso: el motivo sale
en la barra de estado junto al "SQL generado".

## Consecuencias

- El destino de una tabla particionada siempre queda particionado. Si el DESCRIBE
  no da el tipo, se deduce; nunca se cae a tabla plana por eso.
- El destino puede tener una columna que el aliado no pidio: la clave de particion.
  No es una fuga de la regla 003 (el interno solo restringe columnas de datos) — es
  la misma columna por la que ya se filtra el origen, y su valor esta escrito en el
  propio script.
- `partition_cols=None` sigue dando el SQL de siempre: la verificacion de
  solicitudes viejas por regeneracion no se mueve.
- Una solicitud creada sin WHERE contra una tabla particionada ya no se lleva la
  tabla entera: se ejecuta sobre la ultima particion, con el aviso en el log.

## Archivos

- `core/sql_builder.py` — `split_partition_fields`, `infer_partition_type` y el
  parametro `partition_types`.
- `interno/workers.py` — `resolve_partition`: consulta siempre, deduce columnas
  del WHERE si la consulta falla, y devuelve tambien los tipos del origen.
- `interno/ui/adhoc_panel.py` — guarda los tipos del DESCRIBE y los pasa al builder.
- `tests/test_partitioning.py` — la columna no pedida particiona igual; los tres
  origenes del tipo.
- `tests/test_export_sql.py` — los tres caminos de `resolve_partition`.
