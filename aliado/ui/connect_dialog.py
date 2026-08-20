"""Dialogo modal de conexion del aliado a Impala (Sparky, o ODBC de respaldo).

Aparece al arrancar: sin conexion no hay catalogo ni solicitudes. Las
credenciales nunca se guardan; se precargan de las env vars (USERNAME /
PSWD / DSNLZ) y el DSN puede venir del config.ini.

Tambien permite trabajar contra un archivo SQLite con las mismas tablas
(`core/store/sqlite_runner.py`), para cuando el DSN esta intermitente: se
consulta el catalogo y se registran solicitudes ahi, y el equipo interno las
sube a Impala con el restore de siempre.

El backend usado se expone en `backend` y se muestra en la barra de estado:
que el aliado sepa si quedo en Sparky, si cayo a ODBC o si esta sobre archivo.
"""

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from aliado.impala_client import connect_impala, credentials_from_env
from core.store import ddl
from core.store.sqlite_runner import SqliteRunner
from core.ui.repo_worker import RepoWorker


class ConnectDialog(QDialog):
    def __init__(
        self,
        dsn_default: str = "",
        parent=None,
        sqlite_default: str = "",
        use_sqlite: bool = False,
        schema: str = ddl.DEFAULT_SCHEMA,
    ):
        super().__init__(parent)
        self.setWindowTitle("Conexion a Impala - ALIADO")
        self.setModal(True)
        self.runner = None
        self.backend = None
        self.fallback_error = None
        self._worker = None
        self._schema = schema
        creds = credentials_from_env()

        lay = QVBoxLayout(self)
        lay.addWidget(
            QLabel(
                "Conecta con tu usuario de Impala (Sparky si esta disponible,\n"
                "si no el DSN corporativo). Solo se usa para leer el catalogo\n"
                "y registrar solicitudes."
            )
        )
        form = QFormLayout()
        self.user_edit = QLineEdit(creds["username"])
        self.pwd_edit = QLineEdit(creds["password"])
        self.pwd_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.dsn_edit = QLineEdit(dsn_default or creds["dsn"])
        form.addRow("Usuario:", self.user_edit)
        form.addRow("Contrasena:", self.pwd_edit)
        form.addRow("DSN:", self.dsn_edit)
        lay.addLayout(form)

        self.sqlite_check = QCheckBox("Trabajar contra un archivo SQLite")
        self.sqlite_check.setToolTip(
            "Para cuando el DSN falla: las solicitudes quedan en el archivo y "
            "el equipo interno las sube a Impala."
        )
        self.sqlite_check.toggled.connect(self._on_sqlite_toggled)
        lay.addWidget(self.sqlite_check)

        ruta = QWidget()
        ruta_lay = QHBoxLayout(ruta)
        ruta_lay.setContentsMargins(0, 0, 0, 0)
        self.sqlite_edit = QLineEdit(sqlite_default)
        self.sqlite_edit.setPlaceholderText("Ruta del .db compartido")
        self.sqlite_btn = QPushButton("Elegir...")
        self.sqlite_btn.clicked.connect(self._on_pick_sqlite)
        ruta_lay.addWidget(QLabel("Archivo:"))
        ruta_lay.addWidget(self.sqlite_edit)
        ruta_lay.addWidget(self.sqlite_btn)
        lay.addWidget(ruta)

        self.status_label = QLabel("")
        lay.addWidget(self.status_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self._on_connect)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        # al final: _on_sqlite_toggled renombra el boton Ok, que existe recien aqui
        self.sqlite_check.setChecked(use_sqlite)
        self._on_sqlite_toggled(use_sqlite)

    def username(self) -> str:
        return self.user_edit.text().strip()

    def _on_sqlite_toggled(self, on):
        """Sobre archivo no hacen falta DSN ni contrasena; el usuario si."""
        self.sqlite_edit.setEnabled(on)
        self.sqlite_btn.setEnabled(on)
        self.dsn_edit.setEnabled(not on)
        self.pwd_edit.setEnabled(not on)
        self._ok_btn.setText("Abrir archivo" if on else "Conectar")

    def _on_pick_sqlite(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Archivo de coordinacion", self.sqlite_edit.text(),
            "SQLite (*.db);;Todos (*)",
        )
        if path:
            self.sqlite_edit.setText(path)

    def _on_connect(self):
        if self.sqlite_check.isChecked():
            self._open_sqlite()
            return
        dsn = self.dsn_edit.text().strip()
        if not dsn:
            self.status_label.setText("Indica el DSN.")
            return
        user, pwd = self.username(), self.pwd_edit.text()
        self._ok_btn.setEnabled(False)
        self.status_label.setText("Conectando (Sparky, si no ODBC)...")
        self._worker = RepoWorker(lambda: connect_impala(dsn, user, pwd))
        self._worker.finished.connect(self._on_connected)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _open_sqlite(self):
        path = self.sqlite_edit.text().strip()
        if not path:
            self.status_label.setText("Indica la ruta del archivo.")
            return
        if not self.username():
            self.status_label.setText("Indica tu usuario: firma las solicitudes.")
            return
        self._ok_btn.setEnabled(False)
        self.status_label.setText("Abriendo archivo...")
        schema = self._schema
        # ensure_schema=False: crear las guispk_* es tarea de la app interna,
        # aqui solo se abre el archivo que ella ya dejo listo.
        self._worker = RepoWorker(
            lambda: (SqliteRunner(path, schema, ensure_schema=False), "SQLite", None)
        )
        self._worker.finished.connect(self._on_connected)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_connected(self, result):
        self.runner, self.backend, self.fallback_error = result
        self.accept()

    def _on_error(self, msg):
        self._ok_btn.setEnabled(True)
        self.status_label.setText(f"Error: {msg}")
