"""Stub de Sparky para correr la app y los tests sin cluster.

Imita la interfaz interna: obtener_df / ejecutar_query / ejecutar_archivo.
"""

import pandas as pd


class FakeSparky:
    def __init__(self, username=None, password=None, dsn=None):
        self.username = username
        self.password = password
        self.dsn = dsn
        self.executed = []  # historial de queries ejecutadas

    def obtener_df(self, query):
        """Devuelve un DESCRIBE de ejemplo para cualquier tabla."""
        data = [
            ("id_cliente", "bigint", ""),
            ("nombre", "string", ""),
            ("apellido", "varchar(50)", ""),
            ("edad", "int", ""),
            ("saldo", "decimal(12,2)", ""),
            ("fecha_alta", "timestamp", ""),
            ("activo", "boolean", ""),
        ]
        return pd.DataFrame(data, columns=["name", "type", "comment"])

    def ejecutar_query(self, query):
        self.executed.append(query)
        return f"OK: {query[:40]}..."

    def ejecutar_archivo(self, path):
        self.executed.append(f"FILE:{path}")
        return f"OK archivo: {path}"
