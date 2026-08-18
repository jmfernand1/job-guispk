"""QThread generico para llamadas al store de coordinacion.

Con SQLite local las lecturas eran instantaneas y corrian en el hilo de la
UI; contra Impala cada consulta tarda segundos, asi que TODA llamada a un
repo pasa por aqui. Uso via AsyncRepoMixin:

    self._run_async(lambda: repo.list_requests(), self._on_loaded)

El mixin mantiene vivas las referencias a los workers en curso (si se
recolectan, el hilo muere a mitad) y las suelta al terminar.
"""

from PyQt6.QtCore import QThread, pyqtSignal


class RepoWorker(QThread):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - reportar a la UI
            self.error.emit(str(exc))
            return
        self.finished.emit(result)


class AsyncRepoMixin:
    """Agrega _run_async a una ventana/panel Qt."""

    def _run_async(self, fn, on_done, on_error):
        if not hasattr(self, "_repo_workers"):
            self._repo_workers = set()
        worker = RepoWorker(fn)
        self._repo_workers.add(worker)

        def _cleanup(*_):
            self._repo_workers.discard(worker)

        worker.finished.connect(on_done)
        worker.error.connect(on_error)
        worker.finished.connect(_cleanup)
        worker.error.connect(_cleanup)
        worker.start()
        return worker
