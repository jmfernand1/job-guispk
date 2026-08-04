"""Stub de Sparky para correr la app y los tests sin cluster.

Imita la interfaz real que usa SparkyClient: un atributo `.helper` con
obtener_dataframe / ejecutar_consulta / ejecutar_archivo / obtener_ultima_ingestion.
"""

import pandas as pd


class FakeSparky:
    def __init__(self, username=None, password=None, dsn=None):
        self.username = username
        self.password = password
        self.dsn = dsn
        self.executed = []  # historial de queries ejecutadas
        self.helper = self  # SparkyClient usa spk.helper.<metodo>

    def obtener_dataframe(self, query):
        """DESCRIBE y SHOW PARTITIONS de ejemplo para cualquier tabla."""
        if query.strip().upper().startswith("SHOW PARTITIONS"):
            data = [
                ("2026-07-01", 100, 1),
                ("2026-08-01", 200, 1),
                ("Total", 300, 2),
            ]
            return pd.DataFrame(data, columns=["ingestion_day", "#Rows", "#Files"])
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

    def ejecutar_consulta(self, query):
        self.executed.append(query)
        return f"OK: {query[:40]}..."

    def ejecutar_archivo(self, path):
        self.executed.append(f"FILE:{path}")
        return f"OK archivo: {path}"

    def obtener_ultima_ingestion(self, tabla):
        return "2026-08-01"
