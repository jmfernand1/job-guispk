"""Punto de entrada: arranca la QApplication + MainWindow.

Uso normal (cluster real):
    python main.py

Smoke sin cluster (DataFrame DESCRIBE de ejemplo via fake_sparky):
    python main.py --fake
"""

import sys


def _build_factory():
    """Si se pasa --fake usa el stub de Sparky; si no, la Sparky real."""
    if "--fake" in sys.argv:
        from tests.fake_sparky import FakeSparky

        return lambda u, p, d: FakeSparky(u, p, d)
    return None


def main():
    from PyQt6.QtWidgets import QApplication

    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow(sparky_factory=_build_factory())
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
