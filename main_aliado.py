"""Punto de entrada de la app ALIADO.

No usa Sparky: se conecta a Impala con el DSN ODBC del aliado (dialogo de
conexion al arrancar) y trabaja contra las tablas de coordinacion guispk_*
del esquema proceso_enmascarado (SELECT + INSERT; el esquema lo mantiene la
app interna).

    python main_aliado.py            # pide credenciales de Impala
    python main_aliado.py --fake     # smoke sin cluster (store en memoria)

DSN y esquema se resuelven con GUISPK_DSN / config.ini (ver core/config.py).
"""

import sys

from core.config import resolve_settings


def main():
    from PyQt6.QtWidgets import QApplication

    from aliado.ui.main_window import MainWindow

    settings = resolve_settings()
    app = QApplication(sys.argv)

    username = ""
    if "--fake" in sys.argv:
        from tests.fake_impala import new_runner_with_schema

        runner = new_runner_with_schema()
    else:
        from aliado.ui.connect_dialog import ConnectDialog
        from PyQt6.QtWidgets import QDialog

        dialog = ConnectDialog(dsn_default=settings["dsn"])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)
        runner = dialog.runner
        username = dialog.username()

    window = MainWindow(runner, schema=settings["schema"], username=username)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
