# Enmascarador de datos (PyQt6 + Sparky / Impala)

App de escritorio que automatiza el enmascaramiento de datos sensibles antes de
entregarlos a proveedores en la base `proceso_enmascarado`.

Flujo: **Conectar a Impala (vía Sparky) → DESCRIBE de la tabla origen → seleccionar
campos y su enmascaramiento → generar `CREATE TABLE` + `INSERT` → guardar `.sql` y/o
ejecutar en Impala.**

Funciones de enmascaramiento (en la Landing Zone):

- `default.mask_text(campo, 'salt_texto')` → `STRING`
- `default.mask_int(cast(campo as bigint), salt_entero)` → `BIGINT`

> El **mismo salt por tipo** debe usarse en todas partes para mantener integridad
> referencial. La app usa un salt de texto y un salt de entero globales.

## Requisitos

- Python 3.9+
- `PyQt6`, `pandas` (ver `requirements.txt`)
- **Sparky** (librería interna) ya instalada en el entorno

```bash
pip install -r requirements.txt
```

## Variables de entorno

La pantalla de conexión se prerellena desde:

| Variable | Uso |
|----------|-----|
| `USERNAME` | usuario de conexión |
| `PSWD` | contraseña |
| `DSNLZ` | DSN de la Landing Zone |

## Ejecutar

```bash
cd enmascarador
python main.py            # cluster real (usa la Sparky interna)
python main.py --fake     # smoke sin cluster, con un DESCRIBE de ejemplo
```

## Uso

1. **Conectar** — revisa usuario/DSN/contraseña y pulsa *Conectar*.
2. **Tabla origen** — escribe `esquema.tabla` y pulsa *Cargar campos (DESCRIBE)*.
3. **Campos** — marca las columnas a incluir; el combo sugiere `mask_text` /
   `mask_int` / *Sin enmascarar* según el tipo. La vista previa muestra la expresión.
4. **Salts** — fija o genera el salt de texto y el de entero (privados).
5. **Destino** — se prerellena `proceso_enmascarado.<tabla>_enm`, editable.
6. **Acciones** — *Generar SQL* (muestra DDL+DML), *Guardar .sql*, *Ejecutar en Impala*
   (pide confirmación y corre CREATE luego INSERT).

## Tests

```bash
cd enmascarador
python -m pytest tests/ -v
```

Los tests no requieren red ni cluster: validan el mapeo de tipos
(`suggest_masking`) y la generación de SQL (`sql_builder`).

## Estructura

```
enmascarador/
  main.py                  punto de entrada
  app/
    sparky_client.py       adaptador sobre Sparky
    masking.py             reglas tipo→función + expresiones SQL
    sql_builder.py         CREATE TABLE + INSERT
    workers.py             QThread workers (no congelar UI)
    ui/
      main_window.py       ventana principal
      column_table.py      tabla de selección de campos
  tests/
    test_masking.py
    test_sql_builder.py
    fake_sparky.py         stub de Sparky para correr sin cluster
```
