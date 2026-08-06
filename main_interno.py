"""Punto de entrada de la app INTERNA.

Uso normal (cluster real):
    python main_interno.py

Smoke sin cluster (Sparky de mentira + coordinacion en memoria):
    python main_interno.py --fake

La coordinacion (catalogo, solicitudes, historico) vive en Impala, esquema
proceso_enmascarado, tablas guispk_*; se accede con la misma conexion Sparky.
DSN, esquema y ruta del respaldo se resuelven con GUISPK_DSN /
GUISPK_BACKUP_DB / config.ini (ver core/config.py).
"""

import sys

from core.config import resolve_settings


def _build_factories():
    """Con --fake: Sparky stub + coordinacion en memoria (sin cluster)."""
    if "--fake" in sys.argv:
        from tests.fake_impala import new_runner_with_schema
        from tests.fake_sparky import FakeSparky

        fake_runner = new_runner_with_schema()
        return (lambda u, p, d: FakeSparky(u, p, d)), (lambda client: fake_runner)
    return None, None


def main():
    from PyQt6.QtWidgets import QApplication

    from interno.ui.main_window import MainWindow

    settings = resolve_settings()
    sparky_factory, store_runner_factory = _build_factories()

    app = QApplication(sys.argv)
    window = MainWindow(
        sparky_factory=sparky_factory,
        store_runner_factory=store_runner_factory,
        schema=settings["schema"],
        backup_db=settings["backup_db"],
    )
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
