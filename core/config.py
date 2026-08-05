"""Resolucion de la configuracion de coordinacion (Impala).

Orden de resolucion, por clave:
1. Variables de entorno GUISPK_DSN / GUISPK_SCHEMA.
2. config.ini junto al ejecutable (o al cwd en desarrollo), seccion [guispk],
   claves dsn y schema.
3. Defaults: dsn vacio (la UI lo pide al conectar), schema proceso_enmascarado.

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


def resolve_settings() -> dict:
    """{dsn, schema} para la conexion de coordinacion."""
    dsn = os.getenv("GUISPK_DSN", "")
    schema = os.getenv("GUISPK_SCHEMA", "")

    ini_path = os.path.join(_base_dir(), "config.ini")
    if os.path.isfile(ini_path):
        parser = configparser.ConfigParser()
        parser.read(ini_path, encoding="utf-8")
        dsn = dsn or parser.get("guispk", "dsn", fallback="").strip()
        schema = schema or parser.get("guispk", "schema", fallback="").strip()

    return {"dsn": dsn, "schema": schema or ddl.DEFAULT_SCHEMA}
