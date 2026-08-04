"""Conexion SQLite pensada para un archivo en carpeta compartida (OneDrive/SMB).

WAL no es confiable sobre recursos de red, por eso:
- journal_mode=DELETE (el default clasico, seguro en SMB)
- busy_timeout alto + timeout de conexion de 30s
- synchronous=FULL (los writes son raros; priorizamos durabilidad)
- transacciones cortas: cada operacion de repo abre, escribe y cierra.

Patron de uso:
    with open_db(path) as con:
        con.execute(...)
El context manager hace commit al salir sin error y rollback si hay excepcion.
"""

import contextlib
import os
import sqlite3
import time

from core.store import migrations

_RETRIES = 3
_BACKOFF_SECONDS = 0.5


def _connect(path: str) -> sqlite3.Connection:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA synchronous=FULL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


@contextlib.contextmanager
def open_db(path: str):
    """Abre conexion con reintentos ante 'database is locked' y commit/rollback."""
    last_exc = None
    for attempt in range(_RETRIES):
        try:
            con = _connect(path)
            break
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if "locked" not in str(exc).lower():
                raise
            time.sleep(_BACKOFF_SECONDS * (attempt + 1))
    else:
        raise last_exc
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def ensure_db(path: str):
    """Crea/actualiza el esquema (idempotente). Llamar al arrancar cada app."""
    with open_db(path) as con:
        migrations.migrate(con)
