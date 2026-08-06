"""QThread workers para no congelar la UI en operaciones de red (Sparky).

Los que ejecutan SQL (ExecuteRequestWorker, RerunScriptWorker) dejan el script
en el historico con los placeholders de salt intactos: la BD es compartida.
"""

from PyQt6.QtCore import QThread, pyqtSignal

from core import masking, review, sql_builder, states
from core.store import history_repo
from core.sparky_client import extract_columns


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

                if item["partition_where_requested"]:
                    try:
                        fresh_where = self._client.get_partition(src)
                    except Exception as exc:  # noqa: BLE001
                        fresh_where = item["partition_where_requested"]
                        log.append(
                            f"{src}: no se pudo re-resolver la particion ({exc}); "
                            "se usa el WHERE de la solicitud."
                        )
                else:
                    fresh_where = None

                drop, create, insert, script = sql_builder.build_request_script(
                    final, src, dest, fresh_where
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
