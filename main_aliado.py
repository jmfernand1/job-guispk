"""Punto de entrada de la app ALIADO.

Se conecta a Impala por Sparky si el aliado lo tiene disponible y cae al DSN
ODBC si Sparky falla (dialogo de conexion al arrancar). Trabaja contra las
tablas de coordinacion guispk_* del esquema proceso_enmascarado (SELECT +
INSERT; el esquema lo mantiene la app interna).

    python main_aliado.py            # pide credenciales de Impala
    python main_aliado.py --fake     # smoke sin cluster (store en memoria)

DSN y esquema se resuelven con GUISPK_DSN / config.ini (ver core/config.py).
Con GUISPK_BACKEND=sqlite el dialogo arranca apuntando al .db compartido
(GUISPK_SQLITE_PATH), para trabajar mientras el DSN esta intermitente.
"""

import sys

from core.config import BACKEND_SQLITE, resolve_settings


def main():
    from PyQt6.QtWidgets import QApplication

    from aliado.ui.main_window import MainWindow

    settings = resolve_settings()
    app = QApplication(sys.argv)

    username = ""
    backend = ""
    if "--fake" in sys.argv:
        from tests.fake_impala import new_runner_with_schema

        runner = new_runner_with_schema()
        backend = "fake"
    else:
        from aliado.ui.connect_dialog import ConnectDialog
        from PyQt6.QtWidgets import QDialog

        dialog = ConnectDialog(
            dsn_default=settings["dsn"],
            sqlite_default=settings["sqlite_path"],
            use_sqlite=settings["backend"] == BACKEND_SQLITE,
            schema=settings["schema"],
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)
        runner = dialog.runner
        username = dialog.username()
        backend = dialog.backend

    window = MainWindow(
        runner, schema=settings["schema"], username=username, backend=backend
    )
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
