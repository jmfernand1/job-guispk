"""Flujo ad-hoc original: DESCRIBE -> seleccionar -> generar -> ejecutar.

Usa el SparkyClient compartido de la ventana principal (tab Conexion).
"""

import random

from PyQt6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import sql_builder
from core.ui.column_table import ColumnTable
from interno.sparky_client import extract_columns
from interno.workers import DescribeWorker, ExecuteWorker


class AdHocPanel(QWidget):
    def __init__(self, client, status_cb, parent=None):
        super().__init__(parent)
        self.client = client
        self._set_status = status_cb
        self._create_sql = None
        self._insert_sql = None
        self._full_script = None
        self._worker = None  # referencia viva al worker en curso

        root = QVBoxLayout(self)
        root.addWidget(self._build_source_group())
        root.addWidget(self._build_columns_group())
        root.addWidget(self._build_salts_group())
        root.addWidget(self._build_dest_group())
        root.addWidget(self._build_actions_group())

    # -- 1. Tabla origen ----------------------------------------------------
    def _build_source_group(self):
        box = QGroupBox("1. Tabla origen")
        lay = QHBoxLayout(box)
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("esquema.tabla")
        lay.addWidget(QLabel("Origen:"))
        lay.addWidget(self.src_edit)
        self.describe_btn = QPushButton("Cargar campos (DESCRIBE)")
        self.describe_btn.clicked.connect(self.on_describe)
        lay.addWidget(self.describe_btn)
        return box

    # -- 2. Campos ----------------------------------------------------------
    def _build_columns_group(self):
        box = QGroupBox("2. Campos")
        lay = QVBoxLayout(box)
        btn_row = QHBoxLayout()
        all_btn = QPushButton("Seleccionar todo")
        none_btn = QPushButton("Seleccionar ninguno")
        all_btn.clicked.connect(lambda: self.table.set_all_checked(True))
        none_btn.clicked.connect(lambda: self.table.set_all_checked(False))
        btn_row.addWidget(all_btn)
        btn_row.addWidget(none_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        self.table = ColumnTable()
        lay.addWidget(self.table)
        return box

    # -- 3. Salts -----------------------------------------------------------
    def _build_salts_group(self):
        box = QGroupBox("3. Salts (privados - mismo salt por tipo = integridad referencial)")
        lay = QHBoxLayout(box)
        self.text_salt_edit = QLineEdit("saltTexto")
        self.int_salt_edit = QLineEdit("12345")
        self.text_salt_edit.textChanged.connect(self._on_salts_changed)
        self.int_salt_edit.textChanged.connect(self._on_salts_changed)

        lay.addWidget(QLabel("Salt texto:"))
        lay.addWidget(self.text_salt_edit)
        gen_text = QPushButton("Aleatorio")
        gen_text.clicked.connect(self._gen_text_salt)
        lay.addWidget(gen_text)

        lay.addWidget(QLabel("Salt entero:"))
        lay.addWidget(self.int_salt_edit)
        gen_int = QPushButton("Aleatorio")
        gen_int.clicked.connect(self._gen_int_salt)
        lay.addWidget(gen_int)
        return box

    def _on_salts_changed(self, *_):
        self.table.update_salts(self.text_salt_edit.text(), self.int_salt_edit.text())

    def _gen_text_salt(self):
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        self.text_salt_edit.setText("".join(random.choice(alphabet) for _ in range(16)))

    def _gen_int_salt(self):
        self.int_salt_edit.setText(str(random.randint(10000, 999999999)))

    # -- 4. Destino ---------------------------------------------------------
    def _build_dest_group(self):
        box = QGroupBox("4. Tabla destino")
        lay = QHBoxLayout(box)
        self.dest_edit = QLineEdit()
        self.dest_edit.setPlaceholderText("proceso_enmascarado.<tabla>_enm")
        lay.addWidget(QLabel("Destino:"))
        lay.addWidget(self.dest_edit)
        return box

    # -- 5. Acciones --------------------------------------------------------
    def _build_actions_group(self):
        box = QGroupBox("5. Acciones")
        lay = QVBoxLayout(box)
        btn_row = QHBoxLayout()
        self.gen_btn = QPushButton("Generar SQL")
        self.gen_btn.clicked.connect(self.on_generate)
        self.save_btn = QPushButton("Guardar .sql")
        self.save_btn.clicked.connect(self.on_save)
        self.save_btn.setEnabled(False)
        self.exec_btn = QPushButton("Ejecutar en Impala")
        self.exec_btn.clicked.connect(self.on_execute)
        self.exec_btn.setEnabled(False)
        btn_row.addWidget(self.gen_btn)
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.exec_btn)
        lay.addLayout(btn_row)

        self.sql_view = QPlainTextEdit()
        self.sql_view.setReadOnly(True)
        self.sql_view.setPlaceholderText("Aqui aparecera el SQL generado...")
        lay.addWidget(self.sql_view)
        return box

    # ======================================================================
    # Handlers
    # ======================================================================
    def on_describe(self):
        tabla = self.src_edit.text().strip()
        if not tabla:
            QMessageBox.warning(self, "Falta tabla", "Indica la tabla origen.")
            return
        if not self.client.connected:
            QMessageBox.warning(self, "Sin conexion", "Conecta a Sparky primero.")
            return
        self.describe_btn.setEnabled(False)
        self._set_status(f"DESCRIBE {tabla}...")
        self._worker = DescribeWorker(self.client, tabla)
        self._worker.finished.connect(self._on_described)
        self._worker.error.connect(self._on_describe_error)
        self._worker.start()

    def _on_described(self, df):
        self.describe_btn.setEnabled(True)
        columns = extract_columns(df)
        if not columns:
            self._set_status("DESCRIBE no devolvio columnas.")
            return
        self.table.load_columns(columns)
        self.table.update_salts(self.text_salt_edit.text(), self.int_salt_edit.text())
        self._prefill_dest()
        self._set_status(f"{len(columns)} columnas cargadas.")

    def _on_describe_error(self, msg):
        self.describe_btn.setEnabled(True)
        self._set_status(f"Error en DESCRIBE: {msg}")
        QMessageBox.critical(self, "Error en DESCRIBE", msg)

    def _prefill_dest(self):
        src = self.src_edit.text().strip()
        if not src:
            return
        tabla = src.split(".")[-1]
        self.dest_edit.setText(f"proceso_enmascarado.{tabla}_enm")

    def on_generate(self):
        try:
            fields = self.table.selected_fields()
            try:
                where_clause = self.client.get_partition(self.src_edit.text().strip())
            except Exception:  # noqa: BLE001 - tabla sin particiones
                where_clause = None
            create, insert, script = sql_builder.build_script(
                fields,
                self.src_edit.text().strip(),
                self.dest_edit.text().strip(),
                self.text_salt_edit.text(),
                self.int_salt_edit.text(),
                where_clause,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "No se puede generar", str(exc))
            return
        self._create_sql = create
        self._insert_sql = insert
        self._full_script = script
        self.sql_view.setPlainText(script)
        self.save_btn.setEnabled(True)
        self.exec_btn.setEnabled(True)
        self._set_status("SQL generado.")

    def on_save(self):
        if not self._full_script:
            return
        default_name = self.dest_edit.text().strip().replace(".", "_") or "enmascarado"
        path, _ = QFileDialog.getSaveFileName(
            self, "Guardar script SQL", f"{default_name}.sql", "SQL (*.sql)"
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self._full_script)
        self._set_status(f"Guardado en {path}")

    def on_execute(self):
        if not self._create_sql or not self._insert_sql:
            return
        if not self.client.connected:
            QMessageBox.warning(self, "Sin conexion", "Conecta a Sparky primero.")
            return
        resp = QMessageBox.question(
            self,
            "Confirmar ejecucion",
            f"Se ejecutaran CREATE e INSERT en:\n{self.dest_edit.text().strip()}\n\n"
            "Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        self.exec_btn.setEnabled(False)
        self._worker = ExecuteWorker(self.client, [self._create_sql, self._insert_sql])
        self._worker.progress.connect(self._set_status)
        self._worker.finished.connect(self._on_executed)
        self._worker.error.connect(self._on_execute_error)
        self._worker.start()

    def _on_executed(self, _results):
        self.exec_btn.setEnabled(True)
        self._set_status("Ejecucion completada en Impala.")
        QMessageBox.information(
            self, "Listo", "Tabla enmascarada creada e insertada correctamente."
        )

    def _on_execute_error(self, msg):
        self.exec_btn.setEnabled(True)
        self._set_status(f"Error al ejecutar: {msg}")
        QMessageBox.critical(self, "Error al ejecutar", msg)
