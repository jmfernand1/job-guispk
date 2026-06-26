"""Ventana principal de la app de enmascaramiento (PyQt6)."""

import os
import random
import tempfile

from PyQt6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import sql_builder
from app.sparky_client import SparkyClient, credentials_from_env
from app.ui.column_table import ColumnTable
from app.workers import ConnectWorker, DescribeWorker, ExecuteWorker


class MainWindow(QMainWindow):
    def __init__(self, sparky_factory=None):
        super().__init__()
        self.setWindowTitle("Enmascarador de datos - proceso_enmascarado")
        self.resize(980, 800)

        self.client = SparkyClient(sparky_factory=sparky_factory)
        self._create_sql = None
        self._insert_sql = None
        self._full_script = None
        self._worker = None  # referencia viva al worker en curso

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addWidget(self._build_connection_group())
        root.addWidget(self._build_source_group())
        root.addWidget(self._build_columns_group())
        root.addWidget(self._build_salts_group())
        root.addWidget(self._build_dest_group())
        root.addWidget(self._build_actions_group())

        self.status_label = QLabel("Listo.")
        self.statusBar().addWidget(self.status_label)

    # -- 1. Conexion --------------------------------------------------------
    def _build_connection_group(self):
        box = QGroupBox("1. Conexion (Sparky / Impala)")
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
        return box

    # -- 2. Tabla origen ----------------------------------------------------
    def _build_source_group(self):
        box = QGroupBox("2. Tabla origen")
        lay = QHBoxLayout(box)
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("esquema.tabla")
        lay.addWidget(QLabel("Origen:"))
        lay.addWidget(self.src_edit)
        self.describe_btn = QPushButton("Cargar campos (DESCRIBE)")
        self.describe_btn.clicked.connect(self.on_describe)
        lay.addWidget(self.describe_btn)
        return box

    # -- 3. Campos ----------------------------------------------------------
    def _build_columns_group(self):
        box = QGroupBox("3. Campos")
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

    # -- 4. Salts -----------------------------------------------------------
    def _build_salts_group(self):
        box = QGroupBox("4. Salts (privados - mismo salt por tipo = integridad referencial)")
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

    # -- 5. Destino ---------------------------------------------------------
    def _build_dest_group(self):
        box = QGroupBox("5. Tabla destino")
        lay = QHBoxLayout(box)
        self.dest_edit = QLineEdit()
        self.dest_edit.setPlaceholderText("proceso_enmascarado.<tabla>_enm")
        lay.addWidget(QLabel("Destino:"))
        lay.addWidget(self.dest_edit)
        return box

    # -- 6. Acciones --------------------------------------------------------
    def _build_actions_group(self):
        box = QGroupBox("6. Acciones")
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
    def _set_status(self, msg):
        self.status_label.setText(msg)

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
        columns = self._extract_columns(df)
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

    @staticmethod
    def _extract_columns(df):
        """De un DataFrame DESCRIBE saca [(name, type)] hasta fila en blanco."""
        cols = []
        # Impala DESCRIBE: columnas name | type | comment
        name_key = "name" if "name" in df.columns else df.columns[0]
        type_key = "type" if "type" in df.columns else df.columns[1]
        for _, row in df.iterrows():
            name = str(row[name_key]).strip()
            ctype = str(row[type_key]).strip()
            if not name or name.startswith("#"):
                # cabeceras de particiones u otras secciones -> fin de columnas
                break
            cols.append((name, ctype))
        return cols

    def _prefill_dest(self):
        src = self.src_edit.text().strip()
        if not src:
            return
        tabla = src.split(".")[-1]
        self.dest_edit.setText(f"proceso_enmascarado.{tabla}_enm")

    def on_generate(self):
        try:
            fields = self.table.selected_fields()
            create, insert, script = sql_builder.build_script(
                fields,
                self.src_edit.text().strip(),
                self.dest_edit.text().strip(),
                self.text_salt_edit.text(),
                self.int_salt_edit.text(),
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
