"""Resolucion de la configuracion de coordinacion (Impala o SQLite).

Orden de resolucion, por clave:
1. Variables de entorno GUISPK_DSN / GUISPK_SCHEMA / GUISPK_BACKUP_DB /
   GUISPK_BACKEND / GUISPK_SQLITE_PATH.
2. config.ini junto al ejecutable (o al cwd en desarrollo), seccion [guispk],
   claves dsn, schema, backup_db, backend y sqlite_path.
3. Defaults: dsn vacio (la UI lo pide al conectar), schema proceso_enmascarado,
   backup_db vacio (solo lo usa el interno; la UI lo pide o se elige ahi),
   backend impala, sqlite_path el del respaldo (es el mismo archivo).

`backend` elige donde viven las guispk_*: en Impala como siempre, o en un
archivo SQLite mientras el DSN esta intermitente (ver core/store/sqlite_runner).

El config.ini NUNCA lleva credenciales: usuario y password se piden al
arrancar (o se precargan de las env vars USERNAME / PSWD / DSNLZ).
"""

import configparser
import os
import sys

from core.store import ddl


def _base_dir() -> str:
    """Directorio donde buscar config.ini: junto al exe si esta empaquetado."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.getcwd()


BACKEND_IMPALA = "impala"
BACKEND_SQLITE = "sqlite"


def resolve_settings() -> dict:
    """{dsn, schema, backup_db, backend, sqlite_path} de la coordinacion."""
    dsn = os.getenv("GUISPK_DSN", "")
    schema = os.getenv("GUISPK_SCHEMA", "")
    backup_db = os.getenv("GUISPK_BACKUP_DB", "")
    backend = os.getenv("GUISPK_BACKEND", "")
    sqlite_path = os.getenv("GUISPK_SQLITE_PATH", "")

    ini_path = os.path.join(_base_dir(), "config.ini")
    if os.path.isfile(ini_path):
        parser = configparser.ConfigParser()
        parser.read(ini_path, encoding="utf-8")
        dsn = dsn or parser.get("guispk", "dsn", fallback="").strip()
        schema = schema or parser.get("guispk", "schema", fallback="").strip()
        backup_db = backup_db or parser.get(
            "guispk", "backup_db", fallback=""
        ).strip()
        backend = backend or parser.get("guispk", "backend", fallback="").strip()
        sqlite_path = sqlite_path or parser.get(
            "guispk", "sqlite_path", fallback=""
        ).strip()

    backup_db = os.path.expanduser(backup_db) if backup_db else ""
    backend = backend.lower()
    return {
        "dsn": dsn,
        "schema": schema or ddl.DEFAULT_SCHEMA,
        "backup_db": backup_db,
        "backend": backend if backend == BACKEND_SQLITE else BACKEND_IMPALA,
        # sin ruta propia se ofrece la del respaldo: es el mismo archivo, con
        # las mismas tablas, y es el que el equipo ya tiene sincronizado.
        "sqlite_path": os.path.expanduser(sqlite_path) if sqlite_path else backup_db,
    }
