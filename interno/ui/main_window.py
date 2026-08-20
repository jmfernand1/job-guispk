"""Ventana principal de la app INTERNA (PyQt6, pestanas).

Pestanas: Conexion | Inventario | Catalogo | Solicitudes | Historico | Ad-hoc
| Respaldo.

En Solicitudes el interno decide el enmascaramiento de cada columna pedida por
el aliado y ejecuta, rechaza o exporta el SQL a un .sql para editarlo y correrlo
por fuera cuando la corrida necesita otra variante; en Historico consulta y
re-ejecuta los scripts
que ya corrio; en Respaldo copia las guispk_* a un .db en OneDrive y las
restaura desde ahi si las borran.
"""

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
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
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core import config, masking, review, sql_builder, states
from core.store import backup as backup_mod
from core.store import ddl
from core.store.catalog_repo import CatalogRepo
from core.store.history_repo import HistoryRepo
from core.store.requests_repo import RequestsRepo
from core.store.sqlite_runner import SqliteRunner
from core.ui.catalog_browser import CatalogBrowser
from core.ui.column_table import ColumnFilterBar, ColumnTable
from core.ui.repo_worker import AsyncRepoMixin
from core.sparky_client import SparkyClient, credentials_from_env
from core.sparky_runner import SparkyRunner
from interno import salts as salts_mod
from interno.ui.adhoc_panel import AdHocPanel
from interno.ui.data_preview_dialog import DataPreviewDialog
from interno.workers import (
    BuildScriptsWorker,
    CatalogRefreshWorker,
    ConnectWorker,
    ExecuteRequestWorker,
    PreviewWorker,
    RerunScriptWorker,
)

_STATE_FILTER_ALL = "todas"


class MainWindow(QMainWindow, AsyncRepoMixin):
    """La coordinacion vive en Impala: los repos existen solo tras conectar.

    Al conectar a Sparky se construye el runner de coordinacion (por defecto
    SparkyRunner sobre la misma conexion), se asegura el esquema guispk_* y
    recien entonces se cargan inventario/catalogo/solicitudes/historico.
    """

    def __init__(
        self,
        sparky_factory=None,
        store_runner_factory=None,
        schema: str = ddl.DEFAULT_SCHEMA,
        backup_db: str = "",
        backend: str = config.BACKEND_IMPALA,
        sqlite_path: str = "",
    ):
        super().__init__()
        self.setWindowTitle("Enmascarador de datos - INTERNO")
        self.resize(1100, 820)

        self.client = SparkyClient(sparky_factory=sparky_factory)
        self._store_runner_factory = store_runner_factory or SparkyRunner
        self._schema = schema
        # backend de coordinacion elegido: se puede cambiar en el tab Conexion
        self._backend = backend
        self._sqlite_path = sqlite_path or backup_db
        self._store_runner = None  # lo necesita el respaldo (no pasa por repos)
        self.catalog_repo = None
        self.requests_repo = None
        self.history_repo = None
        self._worker = None
        # la vista previa tiene su propio worker: se puede mirar mientras se
        # revisa la solicitud, sin cancelar lo que este corriendo en _worker
        self._preview_worker = None
        self._preview_dialog = None
        self._current_request = None
        self._item_tables = []
        self._history = []
        self._current_script = None
        self._inventory = []
        self._requests = []
        self._export = None  # exportacion de SQL en curso (destino y salts)

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
                history_cb=lambda: self.history_repo,
                who_cb=self._who,
            ),
            "Ad-hoc",
        )
        tabs.addTab(self._build_backup_tab(backup_db), "Respaldo")

        self.status_label = QLabel("Conecta a Sparky para cargar la coordinacion.")
        self.statusBar().addWidget(self.status_label)

    def _set_status(self, msg):
        self.status_label.setText(msg)

    def _who(self):
        return self.user_edit.text().strip() or "interno"

    @property
    def _store_ready(self) -> bool:
        return self.requests_repo is not None

    def _require_store(self) -> bool:
        if not self._store_ready:
            QMessageBox.warning(
                self, "Sin conexion", "Conecta a Sparky primero: la "
                "coordinacion (catalogo, solicitudes, historico) vive en Impala."
            )
            return False
        return True

    def _show_store_error(self, msg):
        self._set_status("Error de coordinacion.")
        QMessageBox.critical(self, "Coordinacion (Impala)", msg)

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
        outer.addWidget(self._build_backend_group())
        outer.addStretch()
        return page

    def _build_backend_group(self):
        """Donde viven las guispk_*: Impala, o el archivo espejo si el DSN falla.

        En modo SQLite se trabaja igual (consultar solicitudes, decidir el
        enmascaramiento) pero no se puede ejecutar nada contra Impala: lo que se
        escriba aqui sube despues con el restore del tab Respaldo.
        """
        box = QGroupBox("Coordinacion: donde viven las tablas guispk_*")
        lay = QVBoxLayout(box)

        radios = QHBoxLayout()
        self.backend_impala_radio = QRadioButton("Impala (Sparky / DSN)")
        self.backend_sqlite_radio = QRadioButton("Archivo SQLite (espejo)")
        grupo = QButtonGroup(self)
        grupo.addButton(self.backend_impala_radio)
        grupo.addButton(self.backend_sqlite_radio)
        if self._backend == config.BACKEND_SQLITE:
            self.backend_sqlite_radio.setChecked(True)
        else:
            self.backend_impala_radio.setChecked(True)
        self.backend_sqlite_radio.toggled.connect(self._on_backend_toggled)
        radios.addWidget(self.backend_impala_radio)
        radios.addWidget(self.backend_sqlite_radio)
        radios.addStretch()
        lay.addLayout(radios)

        ruta = QHBoxLayout()
        self.sqlite_path_edit = QLineEdit(self._sqlite_path)
        self.sqlite_path_edit.setPlaceholderText(
            "Ruta del .db espejo (el mismo del respaldo)"
        )
        self.sqlite_pick_btn = QPushButton("Elegir...")
        self.sqlite_pick_btn.clicked.connect(self._on_pick_sqlite)
        ruta.addWidget(QLabel("Archivo:"))
        ruta.addWidget(self.sqlite_path_edit)
        ruta.addWidget(self.sqlite_pick_btn)
        lay.addLayout(ruta)

        self.backend_hint = QLabel("")
        lay.addWidget(self.backend_hint)
        self._on_backend_toggled(self.backend_sqlite_radio.isChecked())
        return box

    def _on_backend_toggled(self, sqlite_on):
        self.sqlite_path_edit.setEnabled(sqlite_on)
        self.sqlite_pick_btn.setEnabled(sqlite_on)
        self.dsn_edit.setEnabled(not sqlite_on)
        self.pwd_edit.setEnabled(not sqlite_on)
        self.backend_hint.setText(
            "Modo SQLite: se consultan y registran solicitudes en el archivo. "
            "No se puede ejecutar enmascaramiento ni refrescar el catalogo "
            "hasta reconectar a Impala; lo escrito sube con Restaurar."
            if sqlite_on
            else "Modo normal: la coordinacion vive en Impala."
        )

    def _on_pick_sqlite(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Archivo SQLite de coordinacion",
            self.sqlite_path_edit.text() or "guispk.db",
            "SQLite (*.db);;Todos (*)",
        )
        if path:
            self.sqlite_path_edit.setText(path)

    def on_connect(self):
        if self.backend_sqlite_radio.isChecked():
            self._connect_sqlite()
            return
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

    def _connect_sqlite(self):
        """Abre el archivo espejo como coordinacion: sin Sparky de por medio."""
        path = self.sqlite_path_edit.text().strip()
        if not path:
            QMessageBox.warning(
                self, "Falta el archivo", "Indica la ruta del .db espejo."
            )
            return
        self.connect_btn.setEnabled(False)
        self.conn_status.setText("Abriendo archivo...")
        schema = self._schema
        self._run_async(
            lambda: SqliteRunner(path, schema),
            self._on_sqlite_ready,
            self._on_store_error,
        )

    def _on_sqlite_ready(self, runner):
        self._backend = config.BACKEND_SQLITE
        self._sqlite_path = runner.db_path
        self._on_store_ready(runner)
        self.conn_status.setText(f"SQLite: {runner.db_path}")
        self._set_status(
            "Coordinacion en SQLite (sin Impala): no se puede ejecutar "
            "enmascaramiento ni refrescar el catalogo."
        )

    def _on_connected(self, msg):
        self._backend = config.BACKEND_IMPALA
        self.conn_status.setText("Conectado. Preparando coordinacion...")
        self._set_status(msg)
        # El esquema guispk_* lo crea/asegura SOLO la app interna; los aliados
        # operan con SELECT + INSERT. Corre en worker: son varios DDL a Impala.
        client, schema = self.client, self._schema
        factory = self._store_runner_factory

        def _init_store():
            runner = factory(client)
            ddl.ensure_remote_schema(runner, schema)
            return runner

        self._run_async(_init_store, self._on_store_ready, self._on_store_error)

    def _on_store_ready(self, runner):
        self._store_runner = runner
        self.catalog_repo = CatalogRepo(runner, self._schema)
        self.requests_repo = RequestsRepo(runner, self._schema)
        self.history_repo = HistoryRepo(runner, self._schema)
        self.connect_btn.setEnabled(True)
        self.conn_status.setText("Conectado")
        self._set_status("Coordinacion lista.")
        self._reload_inventory()
        self._reload_catalog()
        self._reload_requests()
        self._reload_history()

    def _on_store_error(self, msg):
        self.connect_btn.setEnabled(True)
        self.conn_status.setText("Error")
        self._set_status(f"Error preparando la coordinacion: {msg}")
        QMessageBox.critical(self, "Coordinacion (Impala)", msg)

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
        if not self._store_ready:
            return
        self._run_async(
            self.catalog_repo.list_inventory,
            self._on_inventory_loaded,
            self._show_store_error,
        )

    def _on_inventory_loaded(self, inventory):
        self._inventory = inventory
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
        if not self._require_store():
            return
        if any(e["table_name"] == name for e in self._inventory):
            QMessageBox.warning(self, "Ya existe", f"{name} ya esta en el inventario.")
            return
        desc, who = self.inv_desc_edit.text(), self._who()
        self._run_async(
            lambda: self.catalog_repo.add_table(name, desc, who),
            lambda _: self._on_inventory_added(name),
            self._show_store_error,
        )

    def _on_inventory_added(self, name):
        self.inv_name_edit.clear()
        self.inv_desc_edit.clear()
        self._reload_inventory()
        self._set_status(f"{name} agregada al inventario.")

    def on_inventory_toggle(self):
        row = self.inv_table.currentRow()
        if row < 0 or row >= len(self._inventory):
            return
        if not self._require_store():
            return
        entry, who = self._inventory[row], self._who()
        self._run_async(
            lambda: self.catalog_repo.set_active(
                entry["id"], not entry["active"], who
            ),
            lambda _: (self._reload_inventory(), self._reload_catalog()),
            self._show_store_error,
        )

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
        if not self._store_ready:
            return
        self._run_async(
            self.catalog_repo.latest_schemas,
            self.catalog_browser.load,
            self._show_store_error,
        )

    def on_catalog_refresh(self):
        if not self.client.connected or not self._require_store():
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
        self.export_sql_btn = QPushButton("Exportar SQL (.sql)")
        self.export_sql_btn.setToolTip(
            "Guarda el SQL que se ejecutaria, con las mascaras elegidas en "
            "pantalla, para editarlo y correrlo por fuera de la app."
        )
        self.export_sql_btn.clicked.connect(self.on_export_request_sql)
        for b in (self.execute_btn, self.reject_btn, self.export_sql_btn):
            b.setEnabled(False)
            btn_row.addWidget(b)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        return page

    def _reload_requests(self):
        if not self._store_ready:
            return
        state = self.state_filter.currentText()
        state = None if state == _STATE_FILTER_ALL else state
        self._run_async(
            lambda: self.requests_repo.list_requests(state=state),
            self._on_requests_loaded,
            self._show_store_error,
        )

    def _on_requests_loaded(self, requests):
        self._requests = requests
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
        request_id = self._requests[row]["id"]
        self._set_status("Cargando solicitud...")
        self._run_async(
            lambda: self.requests_repo.get_request(request_id),
            self._on_request_detail_loaded,
            self._show_store_error,
        )

    def _on_request_detail_loaded(self, req):
        if req is None:
            return
        self._current_request = req
        self.req_detail.setPlainText(_render_request(req))
        self._load_item_tables(req)
        self._update_request_buttons()
        self._set_status("Listo.")

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
            # el buscador queda activo aunque la tabla no sea editable: filtrar
            # es solo vista y ayuda a revisar items de cientos de columnas
            page = QWidget()
            page_lay = QVBoxLayout(page)
            page_lay.setContentsMargins(0, 0, 0, 0)
            bar = QHBoxLayout()
            bar.addWidget(ColumnFilterBar(table))
            preview_btn = QPushButton("Vista previa (100 filas)")
            preview_btn.setToolTip(
                "Muestra datos reales del origen para reconocer las columnas "
                "de texto que en realidad guardan enteros."
            )
            preview_btn.clicked.connect(
                lambda _=False, it=item, tb=table: self.on_preview_item(it, tb)
            )
            bar.addWidget(preview_btn)
            page_lay.addLayout(bar)
            page_lay.addWidget(table)
            self.item_tabs.addTab(
                page, f"{item['src_table']} -> {item['dest_table']}"
            )

    def on_preview_item(self, item, table):
        """Trae 100 filas del origen para ver que guardan las columnas de texto."""
        if not self.client.connected:
            QMessageBox.warning(
                self,
                "Sin conexion",
                "La vista previa lee el origen: conecta a Sparky primero.",
            )
            return
        cols = [f["col"] for f in table.selected_fields()] or [
            f["col"] for f in item["fields"]
        ]
        self._preview_dialog = DataPreviewDialog(item["src_table"], self)
        self._preview_dialog.show()
        self._preview_worker = PreviewWorker(
            self.client,
            item["src_table"],
            cols,
            item["partition_where_requested"],
        )
        self._preview_worker.progress.connect(self._set_status)
        self._preview_worker.finished.connect(self._on_preview_ready)
        self._preview_worker.error.connect(self._on_preview_error)
        self._preview_worker.start()

    def _on_preview_ready(self, result):
        df, sql = result
        self._preview_dialog.show_dataframe(df, sql)
        self._set_status("Vista previa lista.")

    def _on_preview_error(self, msg):
        self._preview_dialog.show_error(msg)
        self._set_status("La vista previa fallo.")

    def _update_request_buttons(self):
        req = self._current_request
        state = req["state"] if req else None
        # ejecutar corre DROP/CREATE/INSERT contra Impala: en modo SQLite el
        # store responde, pero no hay con que enmascarar.
        self.execute_btn.setEnabled(
            state == states.ENVIADA and self.client.connected
        )
        # rechazar y decidir solo escriben eventos: eso si funciona offline
        self.reject_btn.setEnabled(state == states.ENVIADA)
        # exportar es solo lectura: sirve tambien para una solicitud ya
        # ejecutada o rechazada, que carga sus columnas en modo consulta.
        self.export_sql_btn.setEnabled(req is not None and bool(self._item_tables))

    def _do_transition(self, to_state, comment=None):
        request_id, who = self._current_request["id"], self._who()
        self._run_async(
            lambda: self.requests_repo.transition(
                request_id, to_state, who, states.ROLE_INTERNO, comment=comment
            ),
            lambda _: self._reload_requests(),
            self._on_transition_error,
        )

    def _on_transition_error(self, msg):
        QMessageBox.warning(self, "No se pudo", msg)
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
        if not self._store_ready:
            return
        search = self.history_search.text()
        self._run_async(
            lambda: self.history_repo.list_scripts(search=search),
            self._on_history_loaded,
            self._show_store_error,
        )

    def _on_history_loaded(self, history):
        self._history = history
        self.history_table.setRowCount(0)
        for h in self._history:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            origen = h["origin"] + (
                f" {h['request_code']}" if h["request_code"] else ""
            )
            for col, val in enumerate(
                [
                    h["event_at"],
                    h["event_by"],
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
            f"Se re-ejecuta tal cual el script de {entry['event_at']}:\n"
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
        self.execute_btn.setEnabled(False)
        repo, who, items = self.requests_repo, self._who(), req["items"]

        def _persist_decisions():
            for item, fields in zip(items, decisions):
                repo.set_item_final_fields(item["id"], fields, who)
            return repo.get_request(req["id"])

        self._set_status("Registrando la decision de enmascaramiento...")
        self._run_async(
            _persist_decisions,
            lambda fresh: self._start_execution(fresh, salts),
            self._on_persist_decisions_error,
        )

    def _on_persist_decisions_error(self, msg):
        self.execute_btn.setEnabled(True)
        QMessageBox.warning(self, "No se pudo", msg)
        self._reload_requests()

    def _start_execution(self, req, salts):
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

    def on_export_request_sql(self):
        """Guarda en un .sql el SQL que se ejecutaria, para editarlo a mano.

        Sale con los salts reales sustituidos, como el panel Ad-hoc: el archivo
        se hizo para ejecutarse por fuera, no para compartirse. Lo que se corra
        desde ahi no pasa por la app y no queda en el historico.
        """
        req = self._current_request
        if not req:
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
        if not self.client.connected:
            resp = QMessageBox.question(
                self,
                "Sin conexion",
                "Sin conexion no se puede re-resolver la particion contra "
                "Impala: el script saldra con el WHERE de la solicitud y el "
                "destino sin particionar.\n\nExportar de todas formas?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar SQL de la solicitud", f"{req['code']}.sql", "SQL (*.sql)"
        )
        if not path:
            return
        self._export = {
            "path": path,
            "salts": salts,
            "code": req["code"],
            "who": self._who(),
        }
        self.export_sql_btn.setEnabled(False)
        self._set_status("Armando el SQL de la solicitud...")
        self._worker = BuildScriptsWorker(self.client, req, decisions)
        self._worker.progress.connect(self._set_status)
        self._worker.finished.connect(self._on_request_sql_built)
        self._worker.error.connect(self._on_request_sql_error)
        self._worker.start()

    def _on_request_sql_built(self, results):
        pendiente, self._export = self._export, None
        self.export_sql_btn.setEnabled(True)
        if not pendiente:
            return
        salts = pendiente["salts"]
        avisos = [r["aviso"] for r in results if r["aviso"]]
        header = [
            f"SQL de la solicitud {pendiente['code']} exportado por "
            f"{pendiente['who']}",
            f"Salt aplicado: {salts['label']}",
            "ATENCION: este archivo lleva los salts REALES ya sustituidos. No "
            "lo subas a un repositorio ni se lo pases al aliado.",
            "Lo que ejecutes desde aqui no pasa por la app: no queda en el "
            "historico ni cambia el estado de la solicitud.",
        ] + avisos
        entries = [
            (
                f"{r['src_table']} -> {r['dest_table']} "
                f"(WHERE {r['partition_where'] or 'sin particion'})",
                r["script"],
            )
            for r in results
        ]
        documento = masking.apply_salts(
            sql_builder.build_export_script(entries, header),
            salts["text_salt"],
            salts["int_salt"],
        )
        try:
            with open(pendiente["path"], "w", encoding="utf-8") as fh:
                fh.write(documento)
        except OSError as exc:
            self._set_status(f"No se pudo guardar el SQL: {exc}")
            QMessageBox.critical(self, "No se pudo guardar", str(exc))
            return
        self._set_status(f"SQL exportado en {pendiente['path']}")
        QMessageBox.information(
            self,
            "SQL exportado",
            f"{len(results)} tabla(s) en:\n{pendiente['path']}\n\n"
            f"Contiene los salts reales ({salts['label']}): no lo compartas.\n"
            + ("\n".join(avisos) if avisos else ""),
        )

    def _on_request_sql_error(self, msg):
        self._export = None
        self.export_sql_btn.setEnabled(True)
        self._set_status(f"Error al armar el SQL: {msg}")
        QMessageBox.critical(self, "No se pudo exportar el SQL", msg)

    # ======================================================================
    # Tab 7: Respaldo
    # ======================================================================
    def _build_backup_tab(self, backup_db: str):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(
            QLabel(
                "Copia de las tablas guispk_* a un archivo .db (OneDrive del "
                "equipo interno).\nSi borran las tablas en "
                f"{self._schema}, el restore las repuebla desde aqui."
            )
        )

        path_row = QHBoxLayout()
        self.backup_path_edit = QLineEdit(backup_db)
        self.backup_path_edit.setPlaceholderText(
            "Ruta del .db de respaldo (config.ini: backup_db)"
        )
        browse_btn = QPushButton("Elegir...")
        browse_btn.clicked.connect(self.on_pick_backup_path)
        path_row.addWidget(QLabel("Archivo:"))
        path_row.addWidget(self.backup_path_edit)
        path_row.addWidget(browse_btn)
        lay.addLayout(path_row)

        btn_row = QHBoxLayout()
        self.backup_btn = QPushButton("Respaldar ahora")
        self.backup_btn.clicked.connect(self.on_backup)
        self.restore_btn = QPushButton("Restaurar desde el respaldo")
        self.restore_btn.clicked.connect(self.on_restore)
        self.backup_info_btn = QPushButton("Ver contenido del respaldo")
        self.backup_info_btn.clicked.connect(self.on_backup_summary)
        btn_row.addWidget(self.backup_btn)
        btn_row.addWidget(self.restore_btn)
        btn_row.addWidget(self.backup_info_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self.backup_log = QPlainTextEdit()
        self.backup_log.setReadOnly(True)
        self.backup_log.setPlaceholderText(
            "El respaldo es una foto: reemplaza el .db entero.\n"
            "El restore solo inserta lo que falta, nunca borra ni duplica."
        )
        lay.addWidget(self.backup_log)
        return page

    def _backup_path(self):
        path = self.backup_path_edit.text().strip()
        if not path:
            QMessageBox.warning(
                self, "Sin archivo",
                "Indica la ruta del .db de respaldo (o ponla en config.ini "
                "como backup_db).",
            )
            return None
        if self._is_active_sqlite(path):
            # respaldar sobre el archivo abierto es DELETE + INSERT contra
            # uno mismo: se perderia justo lo que se quiere guardar.
            QMessageBox.warning(
                self, "Es el archivo en uso",
                "La coordinacion esta abierta sobre ese mismo archivo. "
                "Reconecta a Impala para respaldarlo o restaurarlo, o elige "
                "otra ruta.",
            )
            return None
        return path

    def _is_active_sqlite(self, path) -> bool:
        activo = getattr(self._store_runner, "db_path", None)
        if not activo:
            return False
        return os.path.abspath(os.path.expanduser(path)) == os.path.abspath(activo)

    def _backup_busy(self, busy: bool):
        for btn in (self.backup_btn, self.restore_btn, self.backup_info_btn):
            btn.setEnabled(not busy)

    def on_pick_backup_path(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Archivo de respaldo", self.backup_path_edit.text(),
            "SQLite (*.db)",
        )
        if path:
            self.backup_path_edit.setText(path)

    def on_backup(self):
        if not self._require_store():
            return
        path = self._backup_path()
        if not path:
            return
        runner, schema = self._store_runner, self._schema
        self._backup_busy(True)
        self.backup_log.setPlainText(f"Respaldando en {path}...")
        self._run_async(
            lambda: backup_mod.backup(runner, path, schema),
            self._on_backup_done,
            self._on_backup_error,
        )

    def _on_backup_done(self, counts):
        self._backup_busy(False)
        total = sum(counts.values())
        lines = [f"Respaldo completo: {total} filas."]
        lines += [f"  {t}: {n}" for t, n in counts.items()]
        self.backup_log.setPlainText("\n".join(lines))
        self._set_status("Respaldo completo.")

    def on_restore(self):
        if not self._require_store():
            return
        path = self._backup_path()
        if not path:
            return
        resp = QMessageBox.question(
            self, "Restaurar coordinacion",
            f"Se reinsertan en {self._schema} las filas del respaldo que no "
            "esten ya ahi.\n\nNo borra ni modifica nada de lo que exista "
            "(las guispk_* son append-only) y se puede repetir sin duplicar.\n"
            "Las tablas tienen que existir: si las borraron, reconecta primero "
            "para que se recreen.\n\nContinuar?",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        runner, schema = self._store_runner, self._schema
        self._backup_busy(True)
        self.backup_log.setPlainText(f"Restaurando desde {path}...")
        self._run_async(
            lambda: backup_mod.restore(runner, path, schema),
            self._on_restore_done,
            self._on_backup_error,
        )

    def _on_restore_done(self, counts):
        self._backup_busy(False)
        insertadas = sum(c["insertadas"] for c in counts.values())
        lines = [f"Restore completo: {insertadas} filas insertadas."]
        lines += [
            f"  {t}: {c['insertadas']} insertadas, {c['omitidas']} ya estaban"
            for t, c in counts.items()
        ]
        self.backup_log.setPlainText("\n".join(lines))
        self._set_status("Restore completo.")
        self._reload_inventory()
        self._reload_catalog()
        self._reload_requests()
        self._reload_history()

    def on_backup_summary(self):
        path = self._backup_path()
        if not path:
            return
        self._backup_busy(True)
        self._run_async(
            lambda: backup_mod.summary(path),
            self._on_backup_summary_done,
            self._on_backup_error,
        )

    def _on_backup_summary_done(self, counts):
        self._backup_busy(False)
        lines = ["Contenido del respaldo:"]
        lines += [f"  {t}: {n} filas" for t, n in counts.items()]
        self.backup_log.setPlainText("\n".join(lines))

    def _on_backup_error(self, msg):
        self._backup_busy(False)
        self.backup_log.setPlainText(f"Error: {msg}")
        self._set_status("Error en el respaldo.")
        QMessageBox.critical(self, "Respaldo", msg)


def _render_history_entry(entry) -> str:
    """Ficha de una ejecucion del historico, con su script."""
    lines = [
        f"Ejecutado : {entry['event_at']} por {entry['event_by']}",
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
