"""QThread workers para no congelar la UI en operaciones de red (Sparky)."""

from PyQt6.QtCore import QThread, pyqtSignal


class ConnectWorker(QThread):
    """Conecta a Sparky en segundo plano."""

    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, client, username, password, dsn):
        super().__init__()
        self._client = client
        self._username = username
        self._password = password
        self._dsn = dsn

    def run(self):
        try:
            self._client.connect(self._username, self._password, self._dsn)
            self.finished.emit("Conectado correctamente.")
        except Exception as exc:  # noqa: BLE001 - reportar a la UI
            self.error.emit(str(exc))


class DescribeWorker(QThread):
    """Ejecuta DESCRIBE <tabla> y devuelve el DataFrame."""

    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, client, tabla):
        super().__init__()
        self._client = client
        self._tabla = tabla

    def run(self):
        try:
            df = self._client.describe(self._tabla)
            self.finished.emit(df)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class ExecuteWorker(QThread):
    """Ejecuta una lista de queries en orden (CREATE, luego INSERT)."""

    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, client, queries):
        super().__init__()
        self._client = client
        self._queries = queries

    def run(self):
        try:
            results = []
            for i, q in enumerate(self._queries, start=1):
                self.progress.emit(f"Ejecutando sentencia {i}/{len(self._queries)}...")
                results.append(self._client.run(q))
            self.finished.emit(results)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
