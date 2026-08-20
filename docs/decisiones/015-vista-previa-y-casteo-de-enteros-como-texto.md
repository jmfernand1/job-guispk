# 015 — Vista previa de datos y casteo de enteros guardados como texto

**Tags:** `#sql` · `#enmascaramiento` · `#ui-interno` · **Fecha:** 2026-08-20

## Contexto

Probando la app en produccion aparecieron columnas que son enteros por naturaleza
(numeros de documento, codigos, cuentas) pero que el `DESCRIBE` del origen declara
como `string`. La sugerencia automatica por tipo (`suggest_masking`) las manda a
`mask_text`, que es la decision equivocada: pierde la integridad referencial contra
las mismas entidades enmascaradas con `mask_int` en otras tablas.

Y no se puede saber cual columna es cual mirando el esquema: hay que ver los datos.

Enmascararlas con `mask_int` sin mas tampoco sirve. `default.mask_int` devuelve
`BIGINT`, asi que el destino cambiaria de tipo respecto al origen y el INSERT
fallaria contra una tabla ya creada como string.

## Decision

Dos cosas, las dos del lado interno — es quien decide el enmascaramiento (003).

**1. Vista previa de datos.** Un boton por item de solicitud (y otro en el panel
ad-hoc) que trae una muestra real del origen:

```sql
SELECT <columnas pedidas> FROM <origen> WHERE <particion> LIMIT 100;
```

El WHERE es el de la solicitud; si no trae, el worker resuelve la ultima particion
del origen igual que al ejecutar. Nunca barre la tabla entera. Va por `query_df`
del cliente Sparky (no por el runner del store: son conexiones distintas) y en su
propio `QThread`, como todo lo que consulta Impala.

No se guarda nada: los datos del origen no pasan por las `guispk_*` ni por el
historico. La ventana se cierra y no deja rastro.

**2. Casteo por columna.** Una cuarta opcion en el combo de enmascaramiento,
visible solo en columnas de tipo texto: *"mask_int (entero guardado como texto)"*.
Genera

```sql
cast(default.mask_int(cast(dato as bigint), {{INT_SALT}}) as string)
```

y declara la columna destino como `STRING`, no `BIGINT`: el casteo de vuelta existe
justamente para que el destino conserve el tipo del origen.

## El campo nuevo es opcional a proposito

En el dict de campo aparece una clave `cast` con valor `"bigint"`, y **solo** cuando
se eligio esa opcion — nunca como `None`. Sin la clave, `select_expr` y `dest_type`
devuelven exactamente lo de antes.

Eso no es cosmetico: el interno verifica cada solicitud regenerando su `sql_preview`
y comparandolo con el guardado. La regeneracion parte de `fields` (lo que pidio el
aliado, que nunca lleva `cast`), asi que las solicitudes viejas siguen dando
identico. `tests/test_cast_bigint.py` fija esa regresion.

El aliado no ve la opcion: pide columnas, no mascaras. Su `selected_columns()` ni
mira el combo.

## Que valida la revision

`validate_final_fields` acepta `cast` solo si vale `"bigint"`, la mascara es
`mask_int` y el tipo origen es de texto. Fuera de ahi el `cast(... as bigint)`
fallaria en Impala o cambiaria el tipo del destino sin motivo.

No afloja la regla "el interno solo restringe" (003): elegir el casteo es parte de
elegir la mascara. No agrega columnas que el aliado no pidio ni les cambia el tipo.

## Consecuencias

- Una columna de particion marcada con casteo cae al insert dinamico, igual que
  cualquier columna de particion enmascarada (013): el valor estatico no pasaria
  por la mascara.
- Un `.exe` interno viejo que lea un `fields_final_json` con `cast` lo ignoraria y
  crearia el destino como `BIGINT`. Desplegar primero la app interna.
- La vista previa muestra datos sin enmascarar en pantalla: es la app interna, que
  ya tiene los salts reales y acceso al origen. No cambia quien ve que.

## Archivos

- `core/masking.py` — `CAST_BIGINT`, y el parametro `cast` en `select_expr` /
  `dest_type`.
- `core/sql_builder.py` — `build_preview_select`; `build_create` y `build_insert`
  propagan `f.get("cast")`.
- `core/review.py` — `_validate_cast`.
- `core/ui/column_table.py` — la opcion combinada del combo y su traduccion a
  `(masking, cast)`.
- `interno/workers.py` — `PreviewWorker`.
- `interno/ui/data_preview_dialog.py` — la ventana de muestra.
- `interno/ui/main_window.py`, `interno/ui/adhoc_panel.py` — los botones.
- `tests/test_cast_bigint.py`, `tests/test_column_table_cast.py`,
  `tests/test_preview_worker.py`.
