"""Ventana principal de la app INTERNA (PyQt6, pestanas).

Pestanas: Conexion | Inventario | Catalogo | Solicitudes | Historico | Ad-hoc.

En Solicitudes el interno decide el enmascaramiento de cada columna pedida por
el aliado y ejecuta o rechaza; en Historico consulta y re-ejecuta los scripts
que ya corrio.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core import review, states
from core.store.catalog_repo import CatalogRepo
from core.store.history_repo import HistoryRepo
from core.store.requests_repo import RequestsRepo
from core.ui.catalog_browser import CatalogBrowser
from core.ui.column_table import ColumnTable
from interno import salts as salts_mod
from interno.sparky_client import SparkyClient, credentials_from_env
from interno.ui.adhoc_panel import AdHocPanel
from interno.workers import (
    CatalogRefreshWorker,
    ConnectWorker,
    ExecuteRequestWorker,
    RerunScriptWorker,
)

_STATE_FILTER_ALL = "todas"


class MainWindow(QMainWindow):
    def __init__(self, db_path, sparky_factory=None):
        super().__init__()
        self.setWindowTitle("Enmascarador de datos - INTERNO")
        self.resize(1100, 820)

        self.client = SparkyClient(sparky_factory=sparky_factory)
        self.catalog_repo = CatalogRepo(db_path)
        self.requests_repo = RequestsRepo(db_path)
        self.history_repo = HistoryRepo(db_path)
        self._worker = None
        self._current_request = None
        self._item_tables = []
        self._history = []
        self._current_script = None

        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        tabs.addTab(self._build_connection_tab(), "Conexion")
        tabs.addTab(self._build_inventory_tab(), "Inventario")
        tabs.addTab(self._build_catalog_tab(), "Catalogo")
        tabs.addTab(self._build_requests_tab(), "Solicitudes")
        tabs.addTab(self._build_history_tab(), "Historico")
        tabs.addTab(
            AdHocPanel(
                self.client,
                self._set_status,
                history_repo=self.history_repo,
                who_cb=self._who,
            ),
            "Ad-hoc",
        )

        self.status_label = QLabel("Listo.")
        self.statusBar().addWidget(self.status_label)

        self._reload_inventory()
        self._reload_catalog()
        self._reload_requests()
        self._reload_history()

    def _set_status(self, msg):
        self.status_label.setText(msg)

    def _who(self):
        return self.user_edit.text().strip() or "interno"

    # ======================================================================
    # Tab 1: Conexion
    # ======================================================================
    def _build_connection_tab(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        box = QGroupBox("Conexion (Sparky / Impala)")
        lay = QHBoxLayout(box)
        creds = credentials_from_env()

        self.user_edit = QLineEdit(creds["username"])
        self.dsn_edit = QLineEdit(creds["dsn"])
        self.pwd_edit = QLineEdit(creds["password"])
        self.pwd_edit.setEchoMode(QLineEdit.EchoMode.Password)

        lay.addWidget(QLabel("Usuario:"))
        lay.addWidget(self.user_edit)
        lay.addWidget(QLabel("DSN:"))
        lay.addWidget(self.dsn_edit)
        lay.addWidget(QLabel("Contrasena:"))
        lay.addWidget(self.pwd_edit)

        self.connect_btn = QPushButton("Conectar")
        self.connect_btn.clicked.connect(self.on_connect)
        lay.addWidget(self.connect_btn)

        self.conn_status = QLabel("Sin conectar")
        lay.addWidget(self.conn_status)

        outer.addWidget(box)
        outer.addStretch()
        return page

    def on_connect(self):
        self.connect_btn.setEnabled(False)
        self.conn_status.setText("Conectando...")
        self._worker = ConnectWorker(
            self.client,
            self.user_edit.text(),
            self.pwd_edit.text(),
            self.dsn_edit.text(),
        )
        self._worker.finished.connect(self._on_connected)
        self._worker.error.connect(self._on_connect_error)
        self._worker.start()

    def _on_connected(self, msg):
        self.connect_btn.setEnabled(True)
        self.conn_status.setText("Conectado")
        self._set_status(msg)

    def _on_connect_error(self, msg):
        self.connect_btn.setEnabled(True)
        self.conn_status.setText("Error")
        self._set_status(f"Error de conexion: {msg}")
        QMessageBox.critical(self, "Error de conexion", msg)

    # ======================================================================
    # Tab 2: Inventario
    # ======================================================================
    def _build_inventory_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)

        form = QHBoxLayout()
        self.inv_name_edit = QLineEdit()
        self.inv_name_edit.setPlaceholderText("esquema.tabla")
        self.inv_desc_edit = QLineEdit()
        self.inv_desc_edit.setPlaceholderText("descripcion (opcional)")
        add_btn = QPushButton("Agregar al inventario")
        add_btn.clicked.connect(self.on_inventory_add)
        form.addWidget(QLabel("Tabla:"))
        form.addWidget(self.inv_name_edit)
        form.addWidget(QLabel("Descripcion:"))
        form.addWidget(self.inv_desc_edit)
        form.addWidget(add_btn)
        lay.addLayout(form)

        self.inv_table = QTableWidget(0, 5)
        self.inv_table.setHorizontalHeaderLabels(
            ["Tabla", "Activa", "Descripcion", "Agregada por", "Fecha"]
        )
        self.inv_table.horizontalHeader().setStretchLastSection(True)
        self.inv_table.setColumnWidth(0, 260)
        self.inv_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.inv_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.inv_table)

        toggle_btn = QPushButton("Activar / Desactivar seleccionada")
        toggle_btn.clicked.connect(self.on_inventory_toggle)
        lay.addWidget(toggle_btn)
        return page

    def _reload_inventory(self):
        self._inventory = self.catalog_repo.list_inventory()
        self.inv_table.setRowCount(0)
        for e in self._inventory:
            row = self.inv_table.rowCount()
            self.inv_table.insertRow(row)
            values = [
                e["table_name"],
                "si" if e["active"] else "NO",
                e["description"] or "",
                e["added_by"],
                e["added_at"],
            ]
            for col, val in enumerate(values):
                self.inv_table.setItem(row, col, QTableWidgetItem(val))

    def on_inventory_add(self):
        name = self.inv_name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Falta tabla", "Indica esquema.tabla.")
            return
        try:
            self.catalog_repo.add_table(name, self.inv_desc_edit.text(), self._who())
        except Exception as exc:  # noqa: BLE001 - p.ej. UNIQUE
            QMessageBox.critical(self, "No se pudo agregar", str(exc))
            return
        self.inv_name_edit.clear()
        self.inv_desc_edit.clear()
        self._reload_inventory()
        self._set_status(f"{name} agregada al inventario.")

    def on_inventory_toggle(self):
        row = self.inv_table.currentRow()
        if row < 0 or row >= len(self._inventory):
            return
        entry = self._inventory[row]
        self.catalog_repo.set_active(entry["id"], not entry["active"])
        self._reload_inventory()
        self._reload_catalog()

    # ======================================================================
    # Tab 3: Catalogo
    # ======================================================================
    def _build_catalog_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)

        btn_row = QHBoxLayout()
        self.refresh_catalog_btn = QPushButton("Actualizar catalogo (DESCRIBE inventario)")
        self.refresh_catalog_btn.clicked.connect(self.on_catalog_refresh)
        reload_btn = QPushButton("Recargar vista")
        reload_btn.clicked.connect(self._reload_catalog)
        btn_row.addWidget(self.refresh_catalog_btn)
        btn_row.addWidget(reload_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self.catalog_browser = CatalogBrowser()
        lay.addWidget(self.catalog_browser)

        self.catalog_log = QPlainTextEdit()
        self.catalog_log.setReadOnly(True)
        self.catalog_log.setMaximumHeight(140)
        self.catalog_log.setPlaceholderText("Resultado del ultimo refresh...")
        lay.addWidget(self.catalog_log)
        return page

    def _reload_catalog(self):
        self.catalog_browser.load(self.catalog_repo.latest_schemas())

    def on_catalog_refresh(self):
        if not self.client.connected:
            QMessageBox.warning(self, "Sin conexion", "Conecta a Sparky primero.")
            return
        self.refresh_catalog_btn.setEnabled(False)
        self._worker = CatalogRefreshWorker(self.client, self.catalog_repo, self._who())
        self._worker.progress.connect(self._set_status)
        self._worker.finished.connect(self._on_catalog_refreshed)
        self._worker.error.connect(self._on_catalog_refresh_error)
        self._worker.start()

    def _on_catalog_refreshed(self, summary):
        self.refresh_catalog_btn.setEnabled(True)
        self.catalog_log.setPlainText(summary)
        self._reload_catalog()
        self._set_status("Refresh de catalogo terminado.")

    def _on_catalog_refresh_error(self, msg):
        self.refresh_catalog_btn.setEnabled(True)
        self.catalog_log.setPlainText(msg)
        self._set_status("Error en refresh de catalogo.")

    # ======================================================================
    # Tab 4: Solicitudes
    # ======================================================================
    def _build_requests_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)

        filter_row = QHBoxLayout()
        self.state_filter = QComboBox()
        self.state_filter.addItem(_STATE_FILTER_ALL)
        for s in states.ALL_STATES:
            self.state_filter.addItem(s)
        self.state_filter.setCurrentText(states.ENVIADA)
        self.state_filter.currentTextChanged.connect(lambda *_: self._reload_requests())
        refresh_btn = QPushButton("Refrescar")
        refresh_btn.clicked.connect(self._reload_requests)
        filter_row.addWidget(QLabel("Estado:"))
        filter_row.addWidget(self.state_filter)
        filter_row.addWidget(refresh_btn)
        filter_row.addStretch()
        lay.addLayout(filter_row)

        split = QSplitter()
        self.req_table = QTableWidget(0, 4)
        self.req_table.setHorizontalHeaderLabels(
            ["Codigo", "Estado", "Solicitante", "Creada"]
        )
        self.req_table.horizontalHeader().setStretchLastSection(True)
        self.req_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.req_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.req_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.req_table.itemSelectionChanged.connect(self._on_request_selected)
        split.addWidget(self.req_table)

        right = QSplitter(Qt.Orientation.Vertical)
        self.req_detail = QPlainTextEdit()
        self.req_detail.setReadOnly(True)
        self.req_detail.setPlaceholderText("Selecciona una solicitud...")
        right.addWidget(self.req_detail)

        decision_box = QGroupBox(
            "Enmascaramiento (decision del equipo interno)"
        )
        decision_lay = QVBoxLayout(decision_box)
        decision_lay.addWidget(
            QLabel(
                "Elige la mascara de cada columna. Desmarca las que no deban "
                "salir al destino."
            )
        )
        self.item_tabs = QTabWidget()
        decision_lay.addWidget(self.item_tabs)
        right.addWidget(decision_box)
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 2)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        lay.addWidget(split)

        btn_row = QHBoxLayout()
        self.execute_btn = QPushButton("Ejecutar solicitud")
        self.execute_btn.clicked.connect(self.on_execute_request)
        self.reject_btn = QPushButton("Rechazar")
        self.reject_btn.clicked.connect(self.on_reject)
        for b in (self.execute_btn, self.reject_btn):
            b.setEnabled(False)
            btn_row.addWidget(b)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        return page

    def _reload_requests(self):
        state = self.state_filter.currentText()
        state = None if state == _STATE_FILTER_ALL else state
        self._requests = self.requests_repo.list_requests(state=state)
        self.req_table.setRowCount(0)
        for r in self._requests:
            row = self.req_table.rowCount()
            self.req_table.insertRow(row)
            for col, val in enumerate(
                [r["code"], r["state"], r["requester"], r["created_at"]]
            ):
                self.req_table.setItem(row, col, QTableWidgetItem(val))
        self._current_request = None
        self.req_detail.clear()
        # setRowCount(0) dispara itemSelectionChanged con currentRow() == -1, que
        # sale temprano: hay que limpiar las tablas de columnas explicitamente.
        self._clear_item_tables()
        self._update_request_buttons()

    def _clear_item_tables(self):
        self.item_tabs.clear()
        self._item_tables = []

    def _on_request_selected(self):
        row = self.req_table.currentRow()
        if row < 0 or row >= len(self._requests):
            return
        self._current_request = self.requests_repo.get_request(
            self._requests[row]["id"]
        )
        self.req_detail.setPlainText(_render_request(self._current_request))
        self._load_item_tables(self._current_request)
        self._update_request_buttons()

    def _load_item_tables(self, req):
        """Una ColumnTable por item, precargada con la decision guardada o lo pedido."""
        self._clear_item_tables()
        editable = req["state"] == states.ENVIADA
        try:
            salts = salts_mod.load_salts()
        except Exception:  # noqa: BLE001 - sin salts la vista previa va generica
            salts = None
        for item in req["items"]:
            table = ColumnTable()
            table.load_fields(item["fields_final"] or item["fields"])
            if salts:
                table.update_salts(salts["text_salt"], salts["int_salt"])
            table.setEnabled(editable)
            self._item_tables.append(table)
            self.item_tabs.addTab(
                table, f"{item['src_table']} -> {item['dest_table']}"
            )

    def _update_request_buttons(self):
        req = self._current_request
        state = req["state"] if req else None
        self.execute_btn.setEnabled(state == states.ENVIADA)
        self.reject_btn.setEnabled(state == states.ENVIADA)

    def _do_transition(self, to_state, comment=None):
        try:
            self.requests_repo.transition(
                self._current_request["id"],
                to_state,
                self._who(),
                states.ROLE_INTERNO,
                comment=comment,
            )
        except states.TransitionError as exc:
            QMessageBox.warning(self, "No se pudo", str(exc))
        self._reload_requests()

    def on_reject(self):
        if not self._current_request:
            return
        comment, ok = QInputDialog.getText(
            self, "Rechazar", "Motivo del rechazo (obligatorio):"
        )
        if not ok:
            return
        if not comment.strip():
            QMessageBox.warning(self, "Falta motivo", "Indica el motivo del rechazo.")
            return
        self._do_transition(states.RECHAZADA, comment.strip())

    # ======================================================================
    # Tab 5: Historico de scripts ejecutados
    # ======================================================================
    def _build_history_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)

        top = QHBoxLayout()
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Filtrar por tabla, solicitud o usuario")
        self.history_search.returnPressed.connect(self._reload_history)
        buscar_btn = QPushButton("Buscar")
        buscar_btn.clicked.connect(self._reload_history)
        top.addWidget(QLabel("Buscar:"))
        top.addWidget(self.history_search)
        top.addWidget(buscar_btn)
        lay.addLayout(top)

        split = QSplitter()
        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(
            ["Fecha", "Quien", "Origen", "Destino", "Salt", "Estado"]
        )
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.history_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.itemSelectionChanged.connect(self._on_history_selected)
        split.addWidget(self.history_table)

        self.history_detail = QPlainTextEdit()
        self.history_detail.setReadOnly(True)
        self.history_detail.setPlaceholderText("Selecciona una ejecucion...")
        split.addWidget(self.history_detail)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        lay.addWidget(split)

        btn_row = QHBoxLayout()
        self.rerun_btn = QPushButton("Re-ejecutar script")
        self.rerun_btn.clicked.connect(self.on_rerun_script)
        self.rerun_btn.setEnabled(False)
        self.export_script_btn = QPushButton("Guardar .sql")
        self.export_script_btn.clicked.connect(self.on_export_script)
        self.export_script_btn.setEnabled(False)
        btn_row.addWidget(self.rerun_btn)
        btn_row.addWidget(self.export_script_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        return page

    def _reload_history(self):
        self._history = self.history_repo.list_scripts(
            search=self.history_search.text()
        )
        self.history_table.setRowCount(0)
        for h in self._history:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            origen = h["origin"] + (
                f" {h['request_code']}" if h["request_code"] else ""
            )
            for col, val in enumerate(
                [
                    h["at"],
                    h["who"],
                    origen,
                    h["dest_table"],
                    h["salt_label"] or "-",
                    h["status"],
                ]
            ):
                self.history_table.setItem(row, col, QTableWidgetItem(val))
        self._current_script = None
        self.history_detail.clear()
        self.rerun_btn.setEnabled(False)
        self.export_script_btn.setEnabled(False)

    def _on_history_selected(self):
        row = self.history_table.currentRow()
        if row < 0 or row >= len(self._history):
            return
        self._current_script = self._history[row]
        self.history_detail.setPlainText(
            _render_history_entry(self._current_script)
        )
        self.rerun_btn.setEnabled(True)
        self.export_script_btn.setEnabled(True)

    def on_export_script(self):
        entry = self._current_script
        if not entry:
            return
        nombre = entry["dest_table"].replace(".", "_")
        path, _ = QFileDialog.getSaveFileName(
            self, "Guardar script", f"{nombre}.sql", "SQL (*.sql)"
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(entry["script"])
        self._set_status(f"Script guardado en {path}")

    def on_rerun_script(self):
        entry = self._current_script
        if not entry:
            return
        if not self.client.connected:
            QMessageBox.warning(self, "Sin conexion", "Conecta a Sparky primero.")
            return
        try:
            salts = salts_mod.load_salts()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Salts no disponibles", str(exc))
            return
        aviso = ""
        if entry["salt_label"] and entry["salt_label"] != salts["label"]:
            aviso = (
                f"\nATENCION: se ejecuto con el salt '{entry['salt_label']}' y "
                f"ahora tienes '{salts['label']}'. Los datos enmascarados NO "
                "coincidiran con los de la corrida original.\n"
            )
        resp = QMessageBox.question(
            self,
            "Re-ejecutar script",
            f"Se re-ejecuta tal cual el script de {entry['at']}:\n"
            f"  {entry['src_table']} -> {entry['dest_table']}\n"
            f"  WHERE {entry['partition_where'] or 'sin particion'}\n\n"
            f"La tabla destino se ELIMINA (DROP ... PURGE) y se recrea.\n"
            "La particion NO se re-resuelve: corre el WHERE guardado.\n"
            f"{aviso}\nContinuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        self.rerun_btn.setEnabled(False)
        self._worker = RerunScriptWorker(
            self.client, self.history_repo, entry, salts, self._who()
        )
        self._worker.progress.connect(self._set_status)
        self._worker.finished.connect(self._on_rerun_done)
        self._worker.error.connect(self._on_rerun_error)
        self._worker.start()

    def _on_rerun_done(self, msg):
        self.rerun_btn.setEnabled(True)
        self._set_status(msg)
        QMessageBox.information(self, "Re-ejecutado", msg)
        self._reload_history()

    def _on_rerun_error(self, msg):
        self.rerun_btn.setEnabled(True)
        self._set_status(f"Error al re-ejecutar: {msg}")
        QMessageBox.critical(self, "Error al re-ejecutar", msg)
        self._reload_history()

    def _collect_decisions(self, req):
        """Lee la decision de cada ColumnTable y la valida contra lo solicitado."""
        if len(self._item_tables) != len(req["items"]):
            raise ValueError(
                "La solicitud cambio en pantalla. Refresca e intenta de nuevo."
            )
        decisions = []
        for item, table in zip(req["items"], self._item_tables):
            fields = table.selected_fields()
            try:
                review.validate_final_fields(item["fields"], fields)
            except ValueError as exc:
                raise ValueError(f"{item['src_table']}: {exc}") from exc
            decisions.append(fields)
        return decisions

    def on_execute_request(self):
        req = self._current_request
        if not req or req["state"] != states.ENVIADA:
            return
        if not self.client.connected:
            QMessageBox.warning(self, "Sin conexion", "Conecta a Sparky primero.")
            return
        try:
            salts = salts_mod.load_salts()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Salts no disponibles", str(exc))
            return
        try:
            decisions = self._collect_decisions(req)
        except ValueError as exc:
            QMessageBox.warning(self, "Revisa el enmascaramiento", str(exc))
            return
        resp = QMessageBox.question(
            self,
            "Confirmar ejecucion",
            f"Solicitud {req['code']} ({len(req['items'])} tabla(s)):\n"
            f"{_render_decisions(req, decisions)}\n"
            f"Salt: {salts['label']}\n"
            "La particion se re-resolvera contra Impala antes de insertar.\n"
            "Si la tabla destino ya existe se ELIMINA (DROP ... PURGE) y se "
            "recrea con el resultado de esta corrida.\n"
            "Al ejecutar, la solicitud queda ejecutada a tu nombre.\n\n"
            "Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        # Se persiste la decision antes de ejecutar: queda auditada aunque la
        # ejecucion falle, y al recargar el interno recupera su seleccion.
        try:
            for item, fields in zip(req["items"], decisions):
                self.requests_repo.set_item_final_fields(
                    item["id"], fields, self._who()
                )
        except states.TransitionError as exc:
            QMessageBox.warning(self, "No se pudo", str(exc))
            self._reload_requests()
            return
        req = self.requests_repo.get_request(req["id"])
        self.execute_btn.setEnabled(False)
        self._worker = ExecuteRequestWorker(
            self.client,
            self.requests_repo,
            req,
            salts,
            self._who(),
            history_repo=self.history_repo,
        )
        self._worker.progress.connect(self._set_status)
        self._worker.finished.connect(self._on_request_executed)
        self._worker.error.connect(self._on_request_execute_error)
        self._worker.start()

    def _on_request_executed(self, log):
        self._set_status("Solicitud ejecutada.")
        QMessageBox.information(self, "Ejecutada", log)
        self._reload_requests()

    def _on_request_execute_error(self, msg):
        self._set_status(f"Error al ejecutar solicitud: {msg}")
        QMessageBox.critical(self, "Error al ejecutar", msg)
        self._reload_requests()


def _render_history_entry(entry) -> str:
    """Ficha de una ejecucion del historico, con su script."""
    lines = [
        f"Ejecutado : {entry['at']} por {entry['who']}",
        f"Origen    : {entry['origin']}"
        + (f" ({entry['request_code']})" if entry["request_code"] else ""),
        f"Tablas    : {entry['src_table']} -> {entry['dest_table']}",
        f"WHERE     : {entry['partition_where'] or 'sin particion'}",
        f"Salt      : {entry['salt_label'] or '-'}",
        f"Resultado : {entry['status']}",
    ]
    if entry["error"]:
        lines.append(f"Error     : {entry['error']}")
    lines += ["", "=== Script (los salts se sustituyen al ejecutar) ===", entry["script"]]
    return "\n".join(lines)


def _render_decisions(req, decisions) -> str:
    """Resumen por tabla del enmascaramiento elegido, para el dialogo de confirmacion."""
    bloques = []
    for item, fields in zip(req["items"], decisions):
        lineas = [f"{item['src_table']} -> {item['dest_table']}"]
        lineas.append(review.masking_summary(fields))
        excluidas = review.excluded_columns(item["fields"], fields)
        if excluidas:
            lineas.append(
                f"  Excluidas ({len(excluidas)}): {', '.join(excluidas)}"
            )
        bloques.append("\n".join(lineas))
    return "\n\n".join(bloques) + "\n"


def _render_request(req) -> str:
    """Texto plano con toda la informacion de la solicitud para el revisor."""
    lines = [
        f"Solicitud : {req['code']}   Estado: {req['state']}",
        f"Solicitante: {req['requester']}   Creada: {req['created_at']}",
    ]
    if req["sent_at"]:
        lines.append(f"Enviada   : {req['sent_at']}")
    if req["reviewed_by"]:
        lines.append(
            f"Revision  : {req['reviewed_by']} @ {req['reviewed_at']} "
            f"- {req['review_comment'] or 'sin comentario'}"
        )
    if req["executed_by"]:
        lines.append(f"Ejecucion : {req['executed_by']} @ {req['executed_at']}")
        lines.append(f"WHERE usado: {req['executed_partition_where'] or '-'}")
    for i, item in enumerate(req["items"], start=1):
        lines += [
            "",
            f"--- Item {i}: {item['src_table']} -> {item['dest_table']} ---",
            f"WHERE solicitado: {item['partition_where_requested'] or 'sin particion'}",
            "",
            item["sql_preview"],
        ]
    if req["execution_log"]:
        lines += ["", "=== Log de ejecucion ===", req["execution_log"]]
    return "\n".join(lines)
