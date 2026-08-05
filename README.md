# Enmascarador de datos (PyQt6 + Sparky / Impala)

Motor estandarizado de enmascaramiento para un equipo mixto:

- **Equipo interno (2 personas, acceso total a Impala):** mantiene el inventario
  de tablas autorizadas, publica el catalogo (DESCRIBE), **decide el
  enmascaramiento de cada columna** y **ejecuta** las solicitudes.
- **Aliados (4 personas, sin acceso a zonas internas):** navegan el catalogo
  offline, **eligen las columnas** que necesitan y crean **solicitudes
  formales**. Solo leen la base resultado `proceso_enmascarado`.

Ambos equipos comparten un archivo **SQLite** en una carpeta compartida
(OneDrive / unidad de red) con el inventario, los esquemas capturados, las
solicitudes y la auditoria.

## Flujo

```
INTERNO                       BD COMPARTIDA                  ALIADO
Inventario + DESCRIBE  ──►  catalogo (esquemas)  ──►  navegar catalogo
                                                       elegir columnas
Definir enmascaramiento ◄── solicitud (enviada)  ◄──  enviar solicitud
Ejecutar o Rechazar    ──►  ejecutada + log      ──►  ver estado y mascaras
Historico (re-ejecutar) ◄─  scripts ejecutados
```

Cada ejecucion hace `DROP TABLE IF EXISTS <destino> PURGE` antes del CREATE:
la tabla destino queda siempre con el resultado de la ultima corrida, y
re-ejecutar un script del historico no falla porque el destino ya exista.

El **enmascaramiento es un punto de control del equipo interno**: el aliado pide
columnas, y el interno decide la mascara de cada una y puede excluir las que no
deban salir. Nunca puede agregar una columna que el aliado no pidio: se valida
contra `fields_json` antes de ejecutar.

Ciclo de vida de una solicitud:
`borrador → enviada → ejecutada`, con `enviada → borrador` (retirar)
y `enviada → rechazada → borrador` (corregir y reenviar). Cada movimiento queda
en `audit_log` (quien, cuando, que).

Funciones de enmascaramiento (UDFs en la Landing Zone):

- `default.mask_text(campo, 'salt_texto')` → `STRING`
- `default.mask_int(cast(campo as bigint), salt_entero)` → `BIGINT`

## Una sesion tipica

1. **Interno — Catalogo.** Da de alta la tabla en *Inventario* y corre *Actualizar
   catalogo*: DESCRIBE + SHOW PARTITIONS + ultima ingestion quedan publicados para
   los aliados.
2. **Aliado — Nueva solicitud.** Elige la tabla del catalogo, marca las columnas que
   necesita (no ve enmascaramiento), revisa la vista previa y *Enviar*.
3. **Interno — Solicitudes.** Con el filtro en `enviada` abre la solicitud: arriba el
   detalle, abajo una tabla con las columnas pedidas. Elige la mascara de cada una,
   desmarca lo que no deba salir y *Ejecutar solicitud*. El dialogo resume las mascaras,
   cuantas columnas se excluyen y avisa que el destino se elimina y se recrea.
4. **Aliado — Mis solicitudes.** Ve la solicitud ejecutada con el enmascaramiento que
   aplico el interno y las columnas excluidas; puede exportar el detalle en `.txt`.
5. **Interno — Historico.** El script quedo registrado. Si hay que repetir la corrida
   (se recargo la fuente, fallo a medias), *Re-ejecutar script* lo corre tal cual con
   los salts locales.

## Modelo de datos

Todo vive en el SQLite compartido (`core/store/migrations.py`, versionado por
`PRAGMA user_version`):

| Tabla | Guarda |
|-------|--------|
| `inventory` | tablas autorizadas para los aliados (alta/baja del interno) |
| `table_schemas` | cada captura de esquema: columnas, particiones, ultima ingestion |
| `requests` | la solicitud: estado, solicitante, revision, ejecucion y su log |
| `request_items` | una fila por tabla pedida dentro de la solicitud |
| `script_history` | todo script ejecutado, re-ejecutable |
| `audit_log` | quien hizo que y cuando: `crear`, `transicion`, `decision_enmascaramiento` |

Dos campos de `request_items` concentran el reparto de responsabilidades:

| Campo | Quien lo escribe | Contenido |
|-------|------------------|-----------|
| `fields_json` | aliado | `[{col, type}]` — las columnas que pidio |
| `fields_final_json` | interno | `[{col, type, masking}]` — la mascara que decidio, ya sin las columnas excluidas |
| `sql_preview` | aliado | el texto de la solicitud que confirmo; se regenera y compara antes de ejecutar |

`script_history.script` guarda el SQL **con placeholders** de salt, nunca sustituido.

## Seguridad

- **La BD compartida no contiene secretos**: los aliados pueden leer el archivo.
  Ni credenciales ni salts se guardan ahi.
- El aliado no genera SQL ni elige mascaras: su solicitud es una lista de
  columnas. El SQL lo arma el interno con **placeholders** `{{TEXT_SALT}}` /
  `{{INT_SALT}}` que se sustituyen justo antes de ejecutar.
  Los salts reales viven solo en las maquinas internas, en `~/.guispk/salts.json`:

  ```json
  {"label": "proyecto-x", "text_salt": "<secreto>", "int_salt": 123456789}
  ```

  El mismo par de salts por proyecto preserva la integridad referencial.
- Antes de ejecutar, la app interna **regenera** la solicitud desde los campos
  guardados y la compara con la vista previa almacenada: si difieren
  (alteracion o version distinta), no ejecuta. Ademas valida que toda columna
  que va a crear haya sido solicitada por el aliado y conserve su tipo.
- La particion se **re-resuelve** contra Impala al momento de ejecutar y el
  WHERE realmente usado queda registrado en la solicitud.
- El historico guarda los scripts **con placeholders**, nunca con los salts
  reales: la BD es compartida. Al re-ejecutar se sustituyen con los salts
  locales de la maquina interna.
- El ejecutable del aliado se construye **sin** `sparky_bc` (ver
  `guispk_aliado.spec`): no puede conectarse a Impala.

## Requisitos

- Python 3.9+, `PyQt6`, `pandas` (ver `requirements.txt`)
- Solo interno: **Sparky** (libreria interna) ya instalada en el entorno

```bash
pip install -r requirements.txt
```

## Configuracion de la BD compartida

Orden de resolucion de la ruta (ver `core/config.py`):

1. Variable de entorno `GUISPK_DB`
2. `config.ini` junto al ejecutable (ver `config.ini.example`)
3. `~/.guispk/guispk.db` (fallback de desarrollo)

> OneDrive: marcar la carpeta como **"Conservar siempre en este dispositivo"**
> y evitar editar sin conexion (riesgo de conflictos de sincronizacion). La app
> usa `journal_mode=DELETE` + transacciones cortas porque WAL no es confiable
> en carpetas de red; con 6 usuarios y escrituras esporadicas es suficiente.

## Variables de entorno (solo interno)

| Variable | Uso |
|----------|-----|
| `USERNAME` | usuario de conexion |
| `PSWD` | contrasena |
| `DSNLZ` | DSN de la Landing Zone |

## Ejecutar

```bash
python main_interno.py          # app interna (cluster real via Sparky)
python main_interno.py --fake   # app interna sin cluster (stub de Sparky)
python main_aliado.py           # app aliado (solo BD compartida, sin Sparky)
```

### App interna (pestanas)

1. **Conexion** — credenciales Sparky/Impala.
2. **Inventario** — alta/baja de tablas autorizadas para los aliados.
3. **Catalogo** — *Actualizar catalogo* corre DESCRIBE + SHOW PARTITIONS +
   ultima ingestion de cada tabla activa y publica los esquemas.
4. **Solicitudes** — revisar el detalle, **elegir la mascara de cada columna**
   (y desmarcar las que no deban salir) y decidir en un solo paso:
   *Ejecutar solicitud* (guarda la decision auditada, verifica, re-resuelve
   particion, aplica salts, corre DROP + CREATE + INSERT y marca ejecutada con
   log) o *Rechazar* (con motivo).
5. **Historico** — todos los scripts ejecutados (solicitudes, ad-hoc y
   re-ejecuciones), con buscador por tabla/solicitud/usuario. Permite ver el
   script, guardarlo como `.sql` y **re-ejecutarlo** tal cual (avisa si el salt
   actual no es el de la corrida original).
6. **Ad-hoc** — el flujo original completo para trabajo directo del interno.

### App aliado (pestanas)

1. **Nueva solicitud** — elegir tabla del catalogo, marcar las columnas que
   necesita, generar la vista previa y *Guardar borrador* o *Enviar*. El
   enmascaramiento no se elige aqui.
2. **Mis solicitudes** — estados, comentarios de rechazo, el enmascaramiento que
   aplico el interno, *Enviar*, *Retirar*, *Reabrir rechazada*, *Exportar .txt*.

## Empaquetado (PyInstaller)

```bash
pyinstaller guispk_interno.spec
pyinstaller guispk_aliado.spec   # excluye sparky_bc e interno/
```

Distribuir cada exe con su `config.ini` apuntando a la BD compartida.

## Tests

```bash
python -m pytest tests/ -v
```

Sin red ni cluster (53 tests):

| Archivo | Cubre |
|---------|-------|
| `test_masking.py` | mapeo tipo → funcion de enmascaramiento |
| `test_sql_builder.py` | DROP/CREATE/INSERT, vista previa, `split_statements` |
| `test_regeneration.py` | verificacion anti-alteracion y que el SQL final lleve la decision del interno |
| `test_review.py` | el interno solo puede restringir: nada de columnas no pedidas |
| `test_store.py` | migraciones, repos, ciclo de vida y concurrencia |
| `test_history.py` | historico y que el script guardado nunca lleve salts reales |
| `test_states.py` | maquina de estados y roles |

## Estructura

```
core/                    nucleo compartido (sin Sparky)
  masking.py             reglas tipo→funcion + placeholders de salt
  sql_builder.py         DROP + CREATE + INSERT (+ vista previa de la solicitud)
  review.py              reglas de la decision del interno sobre lo solicitado
  states.py              maquina de estados de solicitudes
  config.py              resolucion de la ruta de la BD compartida
  models.py              serializacion JSON de campos/esquemas
  store/                 SQLite: db, migraciones, catalog/requests/history repos
  ui/                    widgets compartidos: column_table, catalog_browser
interno/                 app interna: sparky_client, salts, workers, ui/
aliado/                  app aliado: ui/ (nunca importa interno/)
main_interno.py          entrada app interna (--fake para smoke)
main_aliado.py           entrada app aliado
tests/                   pytest + fake_sparky (stub con .helper)
docs/decisiones/         una decision de diseno por archivo (ver CHANGELOG.md)
```

## Por que el sistema es asi

Este README describe **como funciona hoy**. El *por que* de cada decision — por que no hay
paso de aprobacion, por que el aliado no elige mascaras, por que cada corrida reemplaza el
destino — esta en [CHANGELOG.md](CHANGELOG.md): un indice con resumen y tags que apunta al
detalle de cada decision en `docs/decisiones/`.
