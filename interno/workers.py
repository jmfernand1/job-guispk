"""QThread workers para no congelar la UI en operaciones de red (Sparky).

Los que ejecutan SQL (ExecuteRequestWorker, RerunScriptWorker) dejan el script
en el historico con los placeholders de salt intactos: la BD es compartida.
"""

from PyQt6.QtCore import QThread, pyqtSignal

from core import masking, review, sql_builder, states
from core.store import history_repo
from core.sparky_client import extract_columns


def resolve_partition(client, item):
    """Re-resuelve contra Impala la particion del origen de un item.

    Devuelve `(part_cols, where, part_types, aviso)`. **El WHERE y las columnas
    de particion siempre viajan juntos**: los dos salen de `SHOW PARTITIONS`, asi
    que no hay corrida que filtre por particion sin escribirla en el destino.

    Se pregunta al origen siempre, traiga o no WHERE la solicitud: el snapshot
    del catalogo puede haberse capturado cuando la tabla no estaba particionada,
    o con el SHOW PARTITIONS caido, y eso dejaba el destino plano. Si la consulta
    falla y la solicitud si traia WHERE, las columnas se deducen de ese WHERE
    (`sql_builder.partition_cols_from_filters`), que tambien salio de SHOW
    PARTITIONS. Sin ninguna de las dos, la tabla es plana de verdad.

    `part_types` es `{columna: tipo}` del DESCRIBE del origen: el destino se
    particiona por todas las columnas de SHOW PARTITIONS, incluidas las que el
    aliado no pidio, y el PARTITIONED BY necesita su tipo. Si el DESCRIBE falla
    no es motivo de aviso: sql_builder deduce el tipo del valor del WHERE.

    Lo comparten el worker que ejecuta y el que solo arma el SQL para exportarlo:
    asi el .sql que el interno se lleva es exactamente el que se ejecutaria.
    """
    src = item["src_table"]
    pedido = item["partition_where_requested"]
    try:
        part_cols, where = client.get_partition_info(src)
    except Exception as exc:  # noqa: BLE001 - sin particion fresca manda la pedida
        if not pedido:
            return None, None, None, None  # tabla sin particiones
        return (
            sql_builder.partition_cols_from_filters(pedido),
            pedido,
            None,
            f"{src}: no se pudo re-resolver la particion ({exc}); "
            "se usa el WHERE de la solicitud y el destino se particiona "
            "por sus columnas.",
        )
    if not pedido:
        return (
            part_cols,
            where,
            _describe_types(client, src),
            f"{src}: la solicitud no traia WHERE de particion; se usa la "
            f"ultima particion del origen ({where}).",
        )
    return part_cols, where, _describe_types(client, src), None


def _describe_types(client, src):
    """`{columna: tipo}` del origen para el PARTITIONED BY, o None si falla.

    No es critico: sin tipos, `sql_builder` los deduce del valor del WHERE.
    """
    try:
        return dict(extract_columns(client.describe(src)))
    except Exception:  # noqa: BLE001 - sin tipos se deducen del WHERE
        return None


class ConnectWorker(QThread):
    """Conecta a Sparky en segundo plano."""

    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, client, username, password, dsn):
        super().__init__()
        self._client = client
        self._username = username
        self._password = password
        self._dsn = dsn

    def run(self):
        try:
            self._client.connect(self._username, self._password, self._dsn)
            self.finished.emit("Conectado correctamente.")
        except Exception as exc:  # noqa: BLE001 - reportar a la UI
            self.error.emit(str(exc))


class DescribeWorker(QThread):
    """Ejecuta DESCRIBE <tabla> y devuelve el DataFrame."""

    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, client, tabla):
        super().__init__()
        self._client = client
        self._tabla = tabla

    def run(self):
        try:
            df = self._client.describe(self._tabla)
            self.finished.emit(df)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class PreviewWorker(QThread):
    """Trae una muestra del origen sin enmascarar, para decidir la mascara.

    Hay columnas que el DESCRIBE declara string pero que guardan enteros: la
    unica forma de distinguirlas es mirar los datos. Se filtra por la particion
    (la de la solicitud, o la ultima del origen si no viene) para no barrer la
    tabla entera, y el LIMIT acota lo que viaja.

    Va por el canal de consulta de Sparky (`query_df`), no por el runner del
    store: son conexiones distintas y esto lee tablas de negocio.
    """

    finished = pyqtSignal(object)  # (DataFrame, sql)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, client, src_table, columns, where=None, limit=100):
        super().__init__()
        self._client = client
        self._src = src_table
        self._columns = list(columns)
        self._where = where
        self._limit = limit

    def run(self):
        try:
            where = self._where
            if where is None:
                try:
                    _, where = self._client.get_partition_info(self._src)
                except Exception:  # noqa: BLE001 - tabla sin particiones
                    where = None
            sql = sql_builder.build_preview_select(
                self._columns, self._src, where, self._limit
            )
            self.progress.emit(f"Leyendo {self._limit} filas de {self._src}...")
            self.finished.emit((self._client.query_df(sql), sql))
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class ExecuteWorker(QThread):
    """Ejecuta una lista de queries en orden (DROP, CREATE, INSERT)."""

    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, client, queries):
        super().__init__()
        self._client = client
        self._queries = queries

    def run(self):
        try:
            results = []
            for i, q in enumerate(self._queries, start=1):
                self.progress.emit(f"Ejecutando sentencia {i}/{len(self._queries)}...")
                results.append(self._client.run(q))
            self.finished.emit(results)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class CatalogRefreshWorker(QThread):
    """Recorre el inventario activo y guarda una captura DESCRIBE por tabla.

    Tolerante a errores por tabla: una tabla que falla no detiene el refresh.
    """

    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, client, catalog_repo, captured_by):
        super().__init__()
        self._client = client
        self._repo = catalog_repo
        self._captured_by = captured_by

    def run(self):
        try:
            inventory = self._repo.list_inventory(active_only=True)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"No se pudo leer el inventario: {exc}")
            return
        if not inventory:
            self.finished.emit("Inventario vacio: no hay tablas activas.")
            return

        ok, failed = 0, []
        for i, entry in enumerate(inventory, start=1):
            tabla = entry["table_name"]
            self.progress.emit(f"[{i}/{len(inventory)}] DESCRIBE {tabla}...")
            try:
                columns = extract_columns(self._client.describe(tabla))
                if not columns:
                    raise ValueError("DESCRIBE no devolvio columnas.")
                try:
                    part_cols, part_where = self._client.get_partition_info(tabla)
                except Exception:  # noqa: BLE001 - tabla sin particiones
                    part_cols, part_where = None, None
                try:
                    ingest = self._client.last_ingest(tabla)
                except Exception:  # noqa: BLE001
                    ingest = None
                self._repo.save_schema_capture(
                    entry["id"],
                    self._captured_by,
                    columns,
                    partition_cols=part_cols,
                    last_partition_where=part_where,
                    last_ingest=str(ingest) if ingest is not None else None,
                )
                ok += 1
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{tabla}: {exc}")

        summary = f"Catalogo actualizado: {ok}/{len(inventory)} tablas."
        if failed:
            summary += "\nFallaron:\n" + "\n".join(failed)
        self.finished.emit(summary)


class ExecuteRequestWorker(QThread):
    """Ejecuta una solicitud enviada: verifica, re-resuelve particion y corre.

    Por cada item:
    1. Regenera lo que pidio el aliado desde fields_json y lo compara con el
       sql_preview guardado (si difieren, aborta: la solicitud fue alterada).
    2. Valida la decision del interno contra lo solicitado: solo puede
       enmascarar y excluir columnas, nunca agregar una que no se pidio.
    3. Re-resuelve la ultima particion en Impala (si falla, usa el WHERE
       snapshot de la solicitud).
    4. Construye el SQL final con las mascaras del interno, sustituye los salts
       placeholder por los reales y ejecuta DROP + CREATE + INSERT (el destino
       se recrea desde cero en cada corrida).
    5. Registra el script en el historico, con los placeholders sin sustituir.
    Al final marca la solicitud como ejecutada con el log completo.
    """

    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(
        self, client, requests_repo, request, salts, executed_by, history_repo=None
    ):
        super().__init__()
        self._client = client
        self._repo = requests_repo
        self._request = request
        self._salts = salts
        self._executed_by = executed_by
        self._history = history_repo

    def _with_salts(self, sql):
        return masking.apply_salts(
            sql, self._salts["text_salt"], self._salts["int_salt"]
        )

    def _record(self, item, script, where):
        """Deja el script en el historico, con placeholders (nunca los salts)."""
        if self._history is None:
            return
        self._history.record(
            who=self._executed_by,
            origin=history_repo.ORIGIN_SOLICITUD,
            src_table=item["src_table"],
            dest_table=item["dest_table"],
            script=script,
            request_code=self._request["code"],
            partition_where=where,
            salt_label=self._salts["label"],
        )

    def run(self):
        req = self._request
        log = [f"Solicitud {req['code']} ejecutada por {self._executed_by}",
               f"Salt aplicado: {self._salts['label']}"]
        executed_wheres = []
        try:
            for i, item in enumerate(req["items"], start=1):
                src, dest = item["src_table"], item["dest_table"]
                self.progress.emit(f"[{i}/{len(req['items'])}] Verificando {src}...")

                if review.is_legacy_item(item["fields"]):
                    # formato viejo: el aliado guardaba el SQL con placeholders
                    _, _, regen = sql_builder.build_request_script(
                        item["fields"], src, dest, item["partition_where_requested"]
                    )
                else:
                    regen = sql_builder.build_request_preview(
                        item["fields"], src, dest, item["partition_where_requested"]
                    )
                if regen != item["sql_preview"]:
                    raise ValueError(
                        f"Lo guardado para {src} no coincide con la regeneracion "
                        "desde los campos: la solicitud fue alterada o generada "
                        "con otra version. No se ejecuta."
                    )

                final = item["fields_final"]
                if not final:
                    raise ValueError(
                        f"No hay decision de enmascaramiento para {src}. "
                        "Revisa las columnas antes de ejecutar."
                    )
                review.validate_final_fields(item["fields"], final)

                # Las columnas de particion se leen del origen al ejecutar (no
                # del snapshot): el destino se crea particionado igual que la
                # fuente, y si la fuente cambio, manda la fuente.
                part_cols, fresh_where, part_types, aviso = resolve_partition(
                    self._client, item
                )
                if aviso:
                    log.append(aviso)

                drop, create, insert, script = sql_builder.build_request_script(
                    final, src, dest, fresh_where, part_cols, part_types
                )
                if part_cols:
                    log.append(
                        f"{dest}: particionado por {', '.join(part_cols)}."
                    )
                self.progress.emit(f"[{i}/{len(req['items'])}] DROP {dest}...")
                self._client.run(drop)
                self.progress.emit(f"[{i}/{len(req['items'])}] CREATE {dest}...")
                self._client.run(self._with_salts(create))
                self.progress.emit(f"[{i}/{len(req['items'])}] INSERT {dest}...")
                self._client.run(self._with_salts(insert))
                self._record(item, script, fresh_where)

                executed_wheres.append(f"{src}: {fresh_where or 'sin particion'}")
                log.append(
                    f"{src} -> {dest}: OK "
                    f"(WHERE {fresh_where or 'sin particion'})"
                )
                log.append(review.masking_summary(final))
                excluidas = review.excluded_columns(item["fields"], final)
                if excluidas:
                    log.append(
                        f"  Columnas excluidas por el interno: {', '.join(excluidas)}"
                    )

            self._repo.transition(
                req["id"],
                states.EJECUTADA,
                self._executed_by,
                states.ROLE_INTERNO,
                execution_log="\n".join(log),
                executed_partition_where=" | ".join(executed_wheres) or None,
            )
            self.finished.emit("\n".join(log))
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class BuildScriptsWorker(QThread):
    """Arma el SQL de una solicitud sin ejecutar nada: es lo que se exporta.

    Hace lo mismo que ExecuteRequestWorker antes de correr una sola sentencia
    (valida la decision contra lo pedido, re-resuelve la particion, construye el
    script con placeholders de salt) y ahi se detiene: no toca la tabla destino,
    ni la solicitud, ni el historico. Va en un QThread porque re-resolver la
    particion consulta Impala y tarda segundos.

    Trabaja con las decisiones que el interno tiene en pantalla, no con
    `fields_final`: exportar sirve justamente para revisar el SQL antes de
    guardar nada.
    """

    finished = pyqtSignal(object)  # [{src_table, dest_table, partition_where, script, aviso}]
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, client, request, decisions):
        super().__init__()
        self._client = client
        self._request = request
        self._decisions = decisions

    def run(self):
        items = self._request["items"]
        try:
            if len(items) != len(self._decisions):
                raise ValueError(
                    "La solicitud cambio en pantalla. Refresca e intenta de nuevo."
                )
            salida = []
            for i, (item, fields) in enumerate(zip(items, self._decisions), start=1):
                src, dest = item["src_table"], item["dest_table"]
                self.progress.emit(f"[{i}/{len(items)}] Armando el SQL de {src}...")
                review.validate_final_fields(item["fields"], fields)
                part_cols, where, part_types, aviso = resolve_partition(
                    self._client, item
                )
                script = sql_builder.build_request_script(
                    fields, src, dest, where, part_cols, part_types
                )[3]
                salida.append(
                    {
                        "src_table": src,
                        "dest_table": dest,
                        "partition_where": where,
                        "script": script,
                        "aviso": aviso,
                    }
                )
            self.finished.emit(salida)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class RerunScriptWorker(QThread):
    """Re-ejecuta tal cual un script del historico con los salts locales.

    No re-resuelve la particion ni regenera nada: corre exactamente el script
    guardado. El DROP inicial hace que la tabla destino quede con el resultado
    de esta corrida.
    """

    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, client, history, entry, salts, who):
        super().__init__()
        self._client = client
        self._history = history
        self._entry = entry
        self._salts = salts
        self._who = who

    def run(self):
        entry = self._entry
        statements = sql_builder.split_statements(entry["script"])
        try:
            for i, stmt in enumerate(statements, start=1):
                self.progress.emit(
                    f"[{i}/{len(statements)}] {stmt.split()[0]} {entry['dest_table']}..."
                )
                self._client.run(
                    masking.apply_salts(
                        stmt, self._salts["text_salt"], self._salts["int_salt"]
                    )
                )
        except Exception as exc:  # noqa: BLE001
            self._record(entry, history_repo.STATUS_ERROR, str(exc))
            self.error.emit(str(exc))
            return
        self._record(entry, history_repo.STATUS_OK, None)
        self.finished.emit(
            f"Re-ejecutado {entry['dest_table']} "
            f"({len(statements)} sentencias, salt {self._salts['label']})."
        )

    def _record(self, entry, status, error):
        if self._history is None:
            return
        self._history.record(
            who=self._who,
            origin=history_repo.ORIGIN_REEJECUCION,
            src_table=entry["src_table"],
            dest_table=entry["dest_table"],
            script=entry["script"],
            request_code=entry["request_code"],
            partition_where=entry["partition_where"],
            salt_label=self._salts["label"],
            status=status,
            error=error,
        )
