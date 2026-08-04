"""Punto de entrada de la app INTERNA.

Uso normal (cluster real):
    python main_interno.py

Smoke sin cluster (Sparky de mentira via fake_sparky):
    python main_interno.py --fake

La ruta de la BD compartida se resuelve con GUISPK_DB / config.ini
(ver core/config.py).
"""

import sys

from core.config import resolve_db_path
from core.store.db import ensure_db


def _build_factory():
    """Si se pasa --fake usa el stub de Sparky; si no, la Sparky real."""
    if "--fake" in sys.argv:
        from tests.fake_sparky import FakeSparky

        return lambda u, p, d: FakeSparky(u, p, d)
    return None


def main():
    from PyQt6.QtWidgets import QApplication

    from interno.ui.main_window import MainWindow

    db_path = resolve_db_path()
    ensure_db(db_path)

    app = QApplication(sys.argv)
    window = MainWindow(db_path, sparky_factory=_build_factory())
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
