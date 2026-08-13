# Enmascarador de datos (PyQt6 + Sparky / Impala)

Motor estandarizado de enmascaramiento para un equipo mixto:

- **Equipo interno (2 personas, acceso total a Impala):** mantiene el inventario
  de tablas autorizadas, publica el catalogo (DESCRIBE), **decide el
  enmascaramiento de cada columna** y **ejecuta** las solicitudes.
- **Aliados (4 personas, sin acceso a zonas internas):** navegan el catalogo,
  **eligen las columnas** que necesitan y crean **solicitudes formales**.
  Su unico acceso a Impala es la zona `proceso_enmascarado`, por Sparky si lo
  tienen disponible o por su DSN ODBC, siempre con credenciales propias.

Ambos equipos se coordinan a traves de tablas **`guispk_*` en Impala**
(esquema `proceso_enmascarado`, append-only): inventario, esquemas capturados,
solicitudes con su auditoria e historico de scripts. La misma zona donde se
escriben las tablas enmascaradas es el punto de encuentro; no hay carpeta
compartida ni servidor propio (antes era un SQLite en OneDrive — ver
decision [006](docs/decisiones/006-coordinacion-impala-append-only.md)).

## Flujo

```
INTERNO                    IMPALA (guispk_*)               ALIADO
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
y `enviada → rechazada → borrador` (corregir y reenviar). Cada movimiento es un
evento en `guispk_request_events` (quien, cuando, que): los eventos son a la
vez el estado y la auditoria.

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
   cuantas columnas se excluyen y avisa que el destino se elimina y se recrea. Si la
   corrida necesita otra variante, *Exportar SQL (.sql)* guarda ese mismo SQL en un
   archivo para editarlo y correrlo por fuera (sale con los salts reales: no se comparte,
   y lo que se ejecute asi no queda en el historico).
4. **Aliado — Mis solicitudes.** Ve la solicitud ejecutada con el enmascaramiento que
   aplico el interno y las columnas excluidas; puede exportar el detalle en `.txt`.
5. **Interno — Historico.** El script quedo registrado. Si hay que repetir la corrida
   (se recargo la fuente, fallo a medias), *Re-ejecutar script* lo corre tal cual con
   los salts locales.

## Modelo de datos

Todo vive en tablas `guispk_*` del esquema `proceso_enmascarado` en Impala
(`core/store/ddl.py`). Son Parquet **insert-only**: Impala no soporta UPDATE,
asi que lo vigente se deriva plegando eventos en orden.

| Tabla | Guarda |
|-------|--------|
| `guispk_inventory_events` | alta/activacion/baja de tablas autorizadas; vigente = ultimo evento por tabla |
| `guispk_schema_captures` | cada captura de esquema: columnas, particiones, ultima ingestion |
| `guispk_request_events` | eventos de solicitud (`crear`, `transicion`, `decision_enmascaramiento`); estado = fold de eventos, y son la auditoria |
| `guispk_request_items` | una fila (inmutable) por tabla pedida dentro de la solicitud |
| `guispk_script_history` | todo script ejecutado, re-ejecutable |

La concurrencia se resuelve sin UPDATE: cada transicion inserta su evento con
el `from_state` que vio; el fold ignora eventos cuyo `from_state` no coincide,
y el proceso que "perdio" recibe `TransitionError` — igual que con el bloqueo
optimista de antes.

Dos campos de `request_items` concentran el reparto de responsabilidades:

| Campo | Quien lo escribe | Contenido |
|-------|------------------|-----------|
| `fields_json` (item) | aliado | `[{col, type}]` — las columnas que pidio |
| `fields_final_json` (evento `decision_enmascaramiento`) | interno | `[{col, type, masking}]` — la mascara que decidio, ya sin las columnas excluidas |
| `sql_preview` (item) | aliado | el texto de la solicitud que confirmo; se regenera y compara antes de ejecutar |

`guispk_script_history.script` guarda el SQL **con placeholders** de salt, nunca sustituido.

## Seguridad

- **Las tablas de coordinacion no contienen secretos**: los aliados las leen.
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
- La tabla destino se crea **particionada igual que el origen**
  (`PARTITIONED BY`), solo por las columnas de particion que el interno dejo
  salir. En Impala esas columnas no se repiten en la lista de columnas y llevan
  su tipo. El INSERT escribe la particion con los valores del mismo WHERE
  (`PARTITION (year = 2026, month = 8, day = 10)`) y por eso esas columnas **no van en
  el SELECT**; si el WHERE no da los valores, o la columna va enmascarada, cae al
  insert dinamico con ellas al final del SELECT.
- El historico guarda los scripts **con placeholders**, nunca con los salts
  reales: las tablas de coordinacion las leen los aliados. Al re-ejecutar se
  sustituyen con los salts locales de la maquina interna.
- El ejecutable del aliado se construye **sin** `interno/` (ver
  `guispk_aliado.spec`). Llega a Impala por Sparky y cae a pyodbc + su propio
  DSN si Sparky falla (`connect_impala`), siempre con permisos SELECT + INSERT
  sobre las `guispk_*` y nada mas. El esquema lo crea y mantiene solo la app
  interna (`ensure_remote_schema`).
- Ninguna columna de las `guispk_*` se llama como una palabra reservada de
  Impala: `event_at` / `event_by` / `actor_role` / `note`, nunca `at` / `who` /
  `role` / `comment` (el `CREATE TABLE` fallaba al desplegar).

## Requisitos

- Python 3.9+, `PyQt6`, `pandas` (ver `requirements.txt`)
- Solo interno: **Sparky** (libreria interna) ya instalada en el entorno
- Aliado: **Sparky** si esta disponible; si no, `pyodbc` + el DSN ODBC de
  Impala corporativo configurado (basta con uno de los dos)

```bash
pip install -r requirements.txt
```

## Configuracion de la coordinacion

Orden de resolucion (ver `core/config.py`):

1. Variables de entorno `GUISPK_DSN` / `GUISPK_SCHEMA` / `GUISPK_BACKUP_DB`
2. `config.ini` junto al ejecutable (ver `config.ini.example`)
3. Default: DSN vacio (la UI lo pide al conectar), esquema `proceso_enmascarado`,
   ruta de respaldo vacia (se elige en la pestana Respaldo)

El `config.ini` **nunca** lleva credenciales: usuario y password se piden al
arrancar (precargados de `USERNAME` / `PSWD` / `DSNLZ` si existen).

Permisos que necesita cada rol sobre `proceso_enmascarado`:

| Rol | Permisos |
|-----|----------|
| interno | CREATE (una vez, para las `guispk_*`) + SELECT/INSERT/DROP en la zona |
| aliado | SELECT + INSERT sobre las `guispk_*`; SELECT sobre las tablas `_enm` |

## Variables de entorno

| Variable | Uso |
|----------|-----|
| `USERNAME` | usuario de conexion (interno y aliado) |
| `PSWD` | contrasena |
| `DSNLZ` | DSN por defecto para el prefill de conexion |
| `GUISPK_BACKUP_DB` | solo interno: `.db` de respaldo de las `guispk_*` (OneDrive) |

## Ejecutar

```bash
python main_interno.py          # app interna (cluster real via Sparky)
python main_interno.py --fake   # app interna sin cluster (stubs en memoria)
python main_aliado.py           # app aliado (pide credenciales de Impala)
python main_aliado.py --fake    # app aliado sin cluster (store en memoria)
```

## Migrar desde el SQLite compartido (una sola vez)

```bash
python -m tools.migrate_sqlite_to_impala "~/OneDrive - Empresa/enmascarado/guispk.db"
```

Lo corre una persona del equipo interno tras coordinar el corte: migra
inventario, ultima captura por tabla, historico completo y solicitudes en
estado final. Las pendientes (`borrador`/`enviada`) se recrean a mano.
`--dry-run` muestra los contadores sin conectar. Despues, archivar el
`guispk.db` como solo-lectura.

### App interna (pestanas)

1. **Conexion** — credenciales Sparky/Impala. Al conectar se asegura el
   esquema `guispk_*` y recien entonces cargan las demas pestanas (la
   coordinacion vive en Impala).
2. **Inventario** — alta/baja de tablas autorizadas para los aliados.
3. **Catalogo** — *Actualizar catalogo* corre DESCRIBE + SHOW PARTITIONS +
   ultima ingestion de cada tabla activa y publica los esquemas.
4. **Solicitudes** — revisar el detalle, **elegir la mascara de cada columna**
   (y desmarcar las que no deban salir) y decidir en un solo paso:
   *Ejecutar solicitud* (guarda la decision auditada, verifica, re-resuelve
   particion, aplica salts, corre DROP + CREATE + INSERT y marca ejecutada con
   log) o *Rechazar* (con motivo). *Exportar SQL (.sql)* arma el mismo SQL que
   ejecutaria — con las mascaras de pantalla y la particion re-resuelta — y lo
   guarda en un archivo con los salts ya sustituidos, para editarlo y correrlo
   por fuera; no persiste la decision ni cambia el estado, asi que tambien sirve
   sobre solicitudes ya ejecutadas.
5. **Historico** — todos los scripts ejecutados (solicitudes, ad-hoc y
   re-ejecuciones), con buscador por tabla/solicitud/usuario. Permite ver el
   script, guardarlo como `.sql` y **re-ejecutarlo** tal cual (avisa si el salt
   actual no es el de la corrida original).
6. **Ad-hoc** — el flujo original completo para trabajo directo del interno.
7. **Respaldo** — copia las cinco tablas `guispk_*` a un `.db` en OneDrive y
   las **restaura** desde ahi si las borran de `proceso_enmascarado`. El
   respaldo es una foto (reemplaza el archivo entero); el restore solo inserta
   las filas que faltan por id, asi que se puede repetir sin duplicar eventos.
   Tras un borrado hay que **reconectar primero** para que se recreen las
   tablas, y despues restaurar.

Para agendar el respaldo (Programador de tareas / cron), sin UI:

```bash
python -m tools.backup_guispk
```

`--restore` repuebla Impala (recrea el esquema primero) y `--summary` informa
que hay en el `.db` sin conectarse. Credenciales por `USERNAME` / `PSWD` /
`DSNLZ`; nunca por argumento. Termina con codigo 0 o 1.

### App aliado (pestanas)

Al arrancar pide usuario/contrasena/DSN de Impala (dialogo modal).

1. **Nueva solicitud** — elegir tabla del catalogo, marcar las columnas que
   necesita, generar la vista previa y *Guardar borrador* o *Enviar*. El
   enmascaramiento no se elige aqui.
2. **Mis solicitudes** — estados, comentarios de rechazo, el enmascaramiento que
   aplico el interno, *Enviar*, *Retirar*, *Reabrir rechazada*, *Exportar .txt*.

## Empaquetado (PyInstaller)

```bash
pyinstaller guispk_interno.spec
pyinstaller guispk_aliado.spec   # excluye interno/; incluye pyodbc (y sparky_bc
                                 # si esta en el entorno de build)
```

Distribuir cada exe con su `config.ini` (DSN + esquema; nunca credenciales).

## Tests

```bash
python -m pytest tests/ -v
```

Sin red ni cluster (56 tests). Los repos corren contra `tests/fake_impala.py`,
un runner que ejecuta el mismo SQL sobre sqlite3 en memoria:

| Archivo | Cubre |
|---------|-------|
| `test_masking.py` | mapeo tipo → funcion de enmascaramiento |
| `test_sql_builder.py` | DROP/CREATE/INSERT, vista previa, `split_statements` |
| `test_regeneration.py` | verificacion anti-alteracion y que el SQL final lleve la decision del interno |
| `test_review.py` | el interno solo puede restringir: nada de columnas no pedidas |
| `test_store.py` | repos append-only, fold de eventos, ciclo de vida y carreras |
| `test_history.py` | historico y que el script guardado nunca lleve salts reales |
| `test_states.py` | maquina de estados y roles |

## Estructura

```
core/                    nucleo compartido (sin Sparky)
  masking.py             reglas tipo→funcion + placeholders de salt
  sql_builder.py         DROP + CREATE + INSERT (+ vista previa de la solicitud)
  review.py              reglas de la decision del interno sobre lo solicitado
  states.py              maquina de estados de solicitudes
  config.py              resolucion de DSN y esquema de coordinacion
  models.py              serializacion JSON de campos/esquemas + ids ordenables
  sparky_client.py       adaptador sobre sparky_bc (import perezoso)
  sparky_runner.py       runner de coordinacion sobre una conexion Sparky
  store/                 coordinacion en Impala: ddl, runner, repos append-only,
                         backup.py (respaldo/restore a SQLite, solo interno)
                         (db.py y migrations.py quedan solo para la migracion)
  ui/                    widgets compartidos: column_table, catalog_browser,
                         repo_worker (toda llamada a repos corre en QThread)
interno/                 app interna: salts, workers, ui/
aliado/                  app aliado: impala_client (Sparky, con fallback a
                         pyodbc), ui/ con dialogo de conexion (nunca importa
                         interno/)
main_interno.py          entrada app interna (--fake para smoke)
main_aliado.py           entrada app aliado (--fake para smoke)
tools/                   migrate_sqlite_to_impala (import unico del guispk.db),
                         backup_guispk (respaldo/restore agendable, sin UI)
tests/                   pytest + fake_sparky + fake_impala
docs/decisiones/         una decision de diseno por archivo (ver CHANGELOG.md)
```

## Por que el sistema es asi

Este README describe **como funciona hoy**. El *por que* de cada decision — por que no hay
paso de aprobacion, por que el aliado no elige mascaras, por que cada corrida reemplaza el
destino — esta en [CHANGELOG.md](CHANGELOG.md): un indice con resumen y tags que apunta al
detalle de cada decision en `docs/decisiones/`.
