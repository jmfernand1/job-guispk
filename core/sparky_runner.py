"""Runner de coordinacion sobre una conexion Sparky (interno y aliado).

Los repos de core/store generan SQL con placeholders `?`; Sparky no soporta
binding, asi que aqui se sustituyen por literales escapados en dialecto
Impala antes de ejecutar. Requiere un SparkyClient ya conectado.

Serializa las llamadas con un lock: varios RepoWorkers pueden disparar a la
vez y la conexion Sparky es una sola.
"""

import threading

from core.store.runner import inline_params


class SparkyRunner:
    def __init__(self, client):
        self._client = client
        self._lock = threading.Lock()

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        import pandas as pd  # el entorno del interno ya lo trae (via Sparky)

        with self._lock:
            df = self._client.query_df(inline_params(sql, params))
        return [
            {k: (None if pd.isna(v) else v) for k, v in rec.items()}
            for rec in df.to_dict(orient="records")
        ]

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._client.run(inline_params(sql, params))
