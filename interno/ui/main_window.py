"""Ventana principal de la app INTERNA (PyQt6, pestanas).

Pestanas: Conexion | Inventario | Catalogo | Solicitudes | Ad-hoc.
"""

from PyQt6.QtWidgets import (
    QComboBox,
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

from core import states
from core.store.catalog_repo import CatalogRepo
from core.store.requests_repo import RequestsRepo
from core.ui.catalog_browser import CatalogBrowser
from interno import salts as salts_mod
from interno.sparky_client import SparkyClient, credentials_from_env
from interno.ui.adhoc_panel import AdHocPanel
from interno.workers import CatalogRefreshWorker, ConnectWorker, ExecuteRequestWorker

_STATE_FILTER_ALL = "todas"


class MainWindow(QMainWindow):
    def __init__(self, db_path, sparky_factory=None):
        super().__init__()
        self.setWindowTitle("Enmascarador de datos - INTERNO")
        self.resize(1100, 820)

        self.client = SparkyClient(sparky_factory=sparky_factory)
        self.catalog_repo = CatalogRepo(db_path)
        self.requests_repo = RequestsRepo(db_path)
        self._worker = None
        self._current_request = None

        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        tabs.addTab(self._build_connection_tab(), "Conexion")
        tabs.addTab(self._build_inventory_tab(), "Inventario")
        tabs.addTab(self._build_catalog_tab(), "Catalogo")
        tabs.addTab(self._build_requests_tab(), "Solicitudes")
        tabs.addTab(AdHocPanel(self.client, self._set_status), "Ad-hoc")

        self.status_label = QLabel("Listo.")
        self.statusBar().addWidget(self.status_label)

        self._reload_inventory()
        self._reload_catalog()
        self._reload_requests()

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

        self.req_detail = QPlainTextEdit()
        self.req_detail.setReadOnly(True)
        self.req_detail.setPlaceholderText("Selecciona una solicitud...")
        split.addWidget(self.req_detail)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
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
        self._update_request_buttons()

    def _on_request_selected(self):
        row = self.req_table.currentRow()
        if row < 0 or row >= len(self._requests):
            return
        self._current_request = self.requests_repo.get_request(
            self._requests[row]["id"]
        )
        self.req_detail.setPlainText(_render_request(self._current_request))
        self._update_request_buttons()

    def _update_request_buttons(self):
        req = self._current_request
        state = req["state"] if req else None
        self.execute_btn.setEnabled(state in states.EJECUTABLES)
        self.reject_btn.setEnabled(state in states.EJECUTABLES)

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

    def on_execute_request(self):
        req = self._current_request
        if not req or req["state"] not in states.EJECUTABLES:
            return
        if not self.client.connected:
            QMessageBox.warning(self, "Sin conexion", "Conecta a Sparky primero.")
            return
        try:
            salts = salts_mod.load_salts()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Salts no disponibles", str(exc))
            return
        tables = "\n".join(
            f"  {i['src_table']} -> {i['dest_table']}" for i in req["items"]
        )
        resp = QMessageBox.question(
            self,
            "Confirmar ejecucion",
            f"Solicitud {req['code']} ({len(req['items'])} tabla(s)):\n{tables}\n\n"
            f"Salt: {salts['label']}\n"
            "La particion se re-resolvera contra Impala antes de insertar.\n"
            "Al ejecutar, la solicitud queda aprobada y ejecutada.\n\n"
            "Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        self.execute_btn.setEnabled(False)
        self._worker = ExecuteRequestWorker(
            self.client, self.requests_repo, req, salts, self._who()
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
