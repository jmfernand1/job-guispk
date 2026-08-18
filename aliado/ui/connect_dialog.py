"""Dialogo modal de conexion del aliado a Impala (Sparky, o ODBC de respaldo).

Aparece al arrancar: sin conexion no hay catalogo ni solicitudes. Las
credenciales nunca se guardan; se precargan de las env vars (USERNAME /
PSWD / DSNLZ) y el DSN puede venir del config.ini.

El backend usado se expone en `backend` y se muestra en la barra de estado:
que el aliado sepa si quedo en Sparky o si cayo a ODBC.
"""

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from aliado.impala_client import connect_impala, credentials_from_env
from core.ui.repo_worker import RepoWorker


class ConnectDialog(QDialog):
    def __init__(self, dsn_default: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Conexion a Impala - ALIADO")
        self.setModal(True)
        self.runner = None
        self.backend = None
        self.fallback_error = None
        self._worker = None
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

        self.status_label = QLabel("")
        lay.addWidget(self.status_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("Conectar")
        buttons.accepted.connect(self._on_connect)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def username(self) -> str:
        return self.user_edit.text().strip()

    def _on_connect(self):
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

    def _on_connected(self, result):
        self.runner, self.backend, self.fallback_error = result
        self.accept()

    def _on_error(self, msg):
        self._ok_btn.setEnabled(True)
        self.status_label.setText(f"Error: {msg}")
