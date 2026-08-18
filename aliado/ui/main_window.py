"""Ventana principal de la app ALIADO (PyQt6).

Trabaja contra las tablas de coordinacion guispk_* en Impala (conexion ODBC
propia del aliado, ya establecida por el dialogo de conexion): navega el
catalogo capturado por el equipo interno, elige las columnas que necesita y
crea solicitudes formales. El enmascaramiento de cada columna lo decide el
equipo interno al ejecutar; el aliado no lo elige ni ve los salts.

Toda llamada a los repos corre en un RepoWorker: contra Impala cada consulta
tarda segundos y no debe congelar la UI.
"""

import getpass

from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QFileDialog,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core import models, review, sql_builder, states
from core.store import ddl
from core.store.catalog_repo import CatalogRepo
from core.store.requests_repo import RequestsRepo
from core.ui.catalog_browser import CatalogBrowser
from core.ui.column_table import ColumnFilterBar, ColumnTable
from core.ui.repo_worker import AsyncRepoMixin


class MainWindow(QMainWindow, AsyncRepoMixin):
    def __init__(
        self,
        runner,
        schema: str = ddl.DEFAULT_SCHEMA,
        username: str = "",
        backend: str = "",
    ):
        super().__init__()
        self.setWindowTitle("Enmascarador de datos - ALIADO")
        self.resize(1100, 820)

        self.catalog_repo = CatalogRepo(runner, schema)
        self.requests_repo = RequestsRepo(runner, schema)
        self._current_entry = None      # entrada del catalogo seleccionada
        self._preview = None            # (fields, src, dest, where, script)
        self._current_request = None
        self._requests = []

        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        tabs.addTab(self._build_new_request_tab(), "Nueva solicitud")
        tabs.addTab(self._build_my_requests_tab(), "Mis solicitudes")

        if username:
            self.requester_edit.setText(username)

        # El backend a la vista: si Sparky fallo y quedo en ODBC, se ve aqui.
        self.status_label = QLabel(f"Listo. Conexion: {backend}" if backend else "Listo.")
        self.statusBar().addWidget(self.status_label)

        self._reload_catalog()
        self._reload_requests()

    def _set_status(self, msg):
        self.status_label.setText(msg)

    def _requester(self):
        return self.requester_edit.text().strip() or getpass.getuser()

    def _show_store_error(self, msg):
        QMessageBox.critical(self, "Coordinacion (Impala)", msg)

    # ======================================================================
    # Tab 1: Nueva solicitud
    # ======================================================================
    def _build_new_request_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)

        id_row = QHBoxLayout()
        self.requester_edit = QLineEdit(getpass.getuser())
        id_row.addWidget(QLabel("Solicitante:"))
        id_row.addWidget(self.requester_edit)
        self.reload_catalog_btn = QPushButton("Recargar catalogo")
        self.reload_catalog_btn.clicked.connect(self._reload_catalog)
        id_row.addWidget(self.reload_catalog_btn)
        id_row.addStretch()
        lay.addLayout(id_row)

        cat_box = QGroupBox("1. Catalogo de tablas autorizadas")
        cat_lay = QVBoxLayout(cat_box)
        self.catalog_browser = CatalogBrowser()
        self.catalog_browser.entrySelected.connect(self._on_entry_selected)
        self.catalog_browser.setMaximumHeight(220)
        cat_lay.addWidget(self.catalog_browser)
        lay.addWidget(cat_box)

        col_box = QGroupBox("2. Columnas solicitadas")
        col_lay = QVBoxLayout(col_box)
        col_lay.addWidget(
            QLabel(
                "Marca las columnas que necesitas. El enmascaramiento de cada "
                "una lo define el equipo interno al revisar la solicitud."
            )
        )
        btn_row = QHBoxLayout()
        all_btn = QPushButton("Seleccionar todo")
        none_btn = QPushButton("Seleccionar ninguno")
        all_btn.clicked.connect(lambda: self.table.set_all_checked(True))
        none_btn.clicked.connect(lambda: self.table.set_all_checked(False))
        btn_row.addWidget(all_btn)
        btn_row.addWidget(none_btn)
        btn_row.addStretch()
        col_lay.addLayout(btn_row)
        self.table = ColumnTable(with_masking=False)
        col_lay.addWidget(ColumnFilterBar(self.table))
        col_lay.addWidget(self.table)
        lay.addWidget(col_box)

        dest_box = QGroupBox("3. Tabla destino")
        dest_lay = QHBoxLayout(dest_box)
        self.dest_edit = QLineEdit()
        self.dest_edit.setPlaceholderText("proceso_enmascarado.<tabla>_enm")
        dest_lay.addWidget(QLabel("Destino:"))
        dest_lay.addWidget(self.dest_edit)
        lay.addWidget(dest_box)

        act_box = QGroupBox("4. Solicitud")
        act_lay = QVBoxLayout(act_box)
        act_row = QHBoxLayout()
        gen_btn = QPushButton("Generar vista previa")
        gen_btn.clicked.connect(self.on_generate)
        self.draft_btn = QPushButton("Guardar borrador")
        self.draft_btn.clicked.connect(lambda: self.on_create_request(send=False))
        self.send_btn = QPushButton("Enviar solicitud")
        self.send_btn.clicked.connect(lambda: self.on_create_request(send=True))
        self.draft_btn.setEnabled(False)
        self.send_btn.setEnabled(False)
        act_row.addWidget(gen_btn)
        act_row.addWidget(self.draft_btn)
        act_row.addWidget(self.send_btn)
        act_lay.addLayout(act_row)

        self.sql_view = QPlainTextEdit()
        self.sql_view.setReadOnly(True)
        self.sql_view.setPlaceholderText(
            "Vista previa de la solicitud (el equipo interno define el "
            "enmascaramiento al ejecutarla)..."
        )
        act_lay.addWidget(self.sql_view)
        lay.addWidget(act_box)
        return page

    def _reload_catalog(self):
        self.reload_catalog_btn.setEnabled(False)
        self._set_status("Cargando catalogo...")
        self._run_async(
            self.catalog_repo.latest_schemas,
            self._on_catalog_loaded,
            self._on_catalog_error,
        )

    def _on_catalog_loaded(self, entries):
        self.reload_catalog_btn.setEnabled(True)
        self.catalog_browser.load(entries)
        self._set_status("Catalogo recargado.")

    def _on_catalog_error(self, msg):
        self.reload_catalog_btn.setEnabled(True)
        self._set_status("Error al cargar el catalogo.")
        self._show_store_error(msg)

    def _on_entry_selected(self, entry):
        self._current_entry = entry
        self.table.load_columns(models.columns_from_json(entry["columns_json"]))
        tabla = entry["table_name"].split(".")[-1]
        self.dest_edit.setText(f"proceso_enmascarado.{tabla}_enm")
        self._invalidate_preview()
        self._set_status(
            f"{entry['table_name']} (esquema capturado {entry['captured_at']})"
        )

    def _invalidate_preview(self):
        self._preview = None
        self.draft_btn.setEnabled(False)
        self.send_btn.setEnabled(False)
        self.sql_view.clear()

    def on_generate(self):
        entry = self._current_entry
        if entry is None:
            QMessageBox.warning(self, "Sin tabla", "Selecciona una tabla del catalogo.")
            return
        try:
            fields = self.table.selected_columns()
            src = entry["table_name"]
            dest = self.dest_edit.text().strip()
            where = entry["last_partition_where"]
            preview = sql_builder.build_request_preview(fields, src, dest, where)
        except ValueError as exc:
            QMessageBox.warning(self, "No se puede generar", str(exc))
            return
        self._preview = (fields, src, dest, where, preview)
        self.sql_view.setPlainText(preview)
        self.draft_btn.setEnabled(True)
        self.send_btn.setEnabled(True)
        self._set_status("Vista previa generada.")

    def on_create_request(self, send: bool):
        if not self._preview:
            return
        fields, src, dest, where, preview = self._preview
        requester = self._requester()
        capture_id = self._current_entry["capture_id"]
        repo = self.requests_repo

        def _create():
            req = repo.create_request(requester)
            repo.add_item(
                req["id"], src, dest, capture_id, fields, where, preview
            )
            if send:
                repo.transition(
                    req["id"], states.ENVIADA, requester, states.ROLE_ALIADO
                )
            return req

        self.draft_btn.setEnabled(False)
        self.send_btn.setEnabled(False)
        self._set_status("Registrando solicitud...")
        self._run_async(
            _create,
            lambda req: self._on_request_created(req, send),
            self._on_create_error,
        )

    def _on_request_created(self, req, send):
        estado = "enviada al equipo interno" if send else "guardada como borrador"
        QMessageBox.information(
            self, "Solicitud creada", f"Solicitud {req['code']} {estado}."
        )
        self._invalidate_preview()
        self._reload_requests()
        self._set_status("Solicitud registrada.")

    def _on_create_error(self, msg):
        self.draft_btn.setEnabled(True)
        self.send_btn.setEnabled(True)
        self._set_status("No se pudo crear la solicitud.")
        QMessageBox.critical(self, "No se pudo crear la solicitud", msg)

    # ======================================================================
    # Tab 2: Mis solicitudes
    # ======================================================================
    def _build_my_requests_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)

        top = QHBoxLayout()
        self.refresh_requests_btn = QPushButton("Refrescar")
        self.refresh_requests_btn.clicked.connect(self._reload_requests)
        top.addWidget(self.refresh_requests_btn)
        top.addStretch()
        lay.addLayout(top)

        split = QSplitter()
        self.req_table = QTableWidget(0, 4)
        self.req_table.setHorizontalHeaderLabels(
            ["Codigo", "Estado", "Creada", "Revision"]
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
        self.send_req_btn = QPushButton("Enviar")
        self.send_req_btn.clicked.connect(lambda: self._transition(states.ENVIADA))
        self.retract_btn = QPushButton("Retirar (a borrador)")
        self.retract_btn.clicked.connect(lambda: self._transition(states.BORRADOR))
        self.reopen_btn = QPushButton("Reabrir rechazada (a borrador)")
        self.reopen_btn.clicked.connect(lambda: self._transition(states.BORRADOR))
        self.export_btn = QPushButton("Exportar .sql")
        self.export_btn.clicked.connect(self.on_export)
        for b in (self.send_req_btn, self.retract_btn, self.reopen_btn, self.export_btn):
            b.setEnabled(False)
            btn_row.addWidget(b)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        return page

    def _reload_requests(self):
        requester = self._requester()
        self.refresh_requests_btn.setEnabled(False)
        self._set_status("Cargando solicitudes...")
        self._run_async(
            lambda: self.requests_repo.list_requests(requester=requester),
            self._on_requests_loaded,
            self._on_requests_error,
        )

    def _on_requests_loaded(self, requests):
        self.refresh_requests_btn.setEnabled(True)
        self._requests = requests
        self.req_table.setRowCount(0)
        for r in self._requests:
            row = self.req_table.rowCount()
            self.req_table.insertRow(row)
            review_txt = r["review_comment"] or ""
            for col, val in enumerate(
                [r["code"], r["state"], r["created_at"], review_txt]
            ):
                self.req_table.setItem(row, col, QTableWidgetItem(val))
        self._current_request = None
        self.req_detail.clear()
        self._update_buttons()
        self._set_status("Solicitudes cargadas.")

    def _on_requests_error(self, msg):
        self.refresh_requests_btn.setEnabled(True)
        self._set_status("Error al cargar solicitudes.")
        self._show_store_error(msg)

    def _on_request_selected(self):
        row = self.req_table.currentRow()
        if row < 0 or row >= len(self._requests):
            return
        request_id = self._requests[row]["id"]
        self._set_status("Cargando detalle...")
        self._run_async(
            lambda: self.requests_repo.get_request(request_id),
            self._on_request_detail_loaded,
            self._on_requests_error,
        )

    def _on_request_detail_loaded(self, req):
        if req is None:
            return
        self._current_request = req
        self.req_detail.setPlainText(_render_request(req))
        self._update_buttons()
        self._set_status("Listo.")

    def _update_buttons(self):
        req = self._current_request
        state = req["state"] if req else None
        self.send_req_btn.setEnabled(state == states.BORRADOR)
        self.retract_btn.setEnabled(state == states.ENVIADA)
        self.reopen_btn.setEnabled(state == states.RECHAZADA)
        self.export_btn.setEnabled(req is not None)

    def _transition(self, to_state):
        if not self._current_request:
            return
        request_id = self._current_request["id"]
        requester = self._requester()
        self._set_status("Actualizando solicitud...")
        self._run_async(
            lambda: self.requests_repo.transition(
                request_id, to_state, requester, states.ROLE_ALIADO
            ),
            lambda _: self._reload_requests(),
            self._on_transition_error,
        )

    def _on_transition_error(self, msg):
        QMessageBox.warning(self, "No se pudo", msg)
        self._reload_requests()

    def on_export(self):
        req = self._current_request
        if not req:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar detalle", f"{req['code']}.txt", "Texto (*.txt)"
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_render_request(req))
        self._set_status(f"Exportado en {path}")


def _render_request(req) -> str:
    """Detalle de la solicitud para el aliado: lo pedido y lo que decidio el interno."""
    lines = [
        f"Solicitud : {req['code']}   Estado: {req['state']}",
        f"Creada    : {req['created_at']}",
    ]
    if req["review_comment"]:
        lines.append(f"Revision  : {req['reviewed_by']} - {req['review_comment']}")
    if req["executed_at"]:
        lines.append(f"Ejecutada : {req['executed_at']} por {req['executed_by']}")
    for item in req["items"]:
        lines += ["", item["sql_preview"]]
        final = item["fields_final"]
        if final and req["executed_at"]:
            lines += [
                "",
                "Enmascaramiento aplicado por el equipo interno:",
                review.masking_summary(final),
            ]
            excluidas = review.excluded_columns(item["fields"], final)
            if excluidas:
                lines.append(
                    "Columnas excluidas por el equipo interno: "
                    + ", ".join(excluidas)
                )
    return "\n".join(lines)
