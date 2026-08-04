"""Punto de entrada de la app ALIADO.

No requiere Sparky ni credenciales de Impala: trabaja solo contra la BD
compartida (catalogo publicado por el equipo interno + solicitudes).

    python main_aliado.py

La ruta de la BD compartida se resuelve con GUISPK_DB / config.ini
(ver core/config.py).
"""

import sys

from core.config import resolve_db_path
from core.store.db import ensure_db


def main():
    from PyQt6.QtWidgets import QApplication

    from aliado.ui.main_window import MainWindow

    db_path = resolve_db_path()
    ensure_db(db_path)

    app = QApplication(sys.argv)
    window = MainWindow(db_path)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
