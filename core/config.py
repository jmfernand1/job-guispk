"""Resolucion de la ruta de la BD compartida.

Orden de resolucion:
1. Variable de entorno GUISPK_DB.
2. config.ini junto al ejecutable (o al cwd en desarrollo), seccion [guispk],
   clave db_path.
3. ~/.guispk/guispk.db (fallback para desarrollo local).

La BD vive tipicamente en una carpeta compartida (OneDrive / unidad de red)
accesible por aliados e internos. No contiene secretos: ni credenciales ni salts.
"""

import configparser
import os
import sys


def _base_dir() -> str:
    """Directorio donde buscar config.ini: junto al exe si esta empaquetado."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.getcwd()


def resolve_db_path() -> str:
    env = os.getenv("GUISPK_DB")
    if env:
        return os.path.expanduser(env)

    ini_path = os.path.join(_base_dir(), "config.ini")
    if os.path.isfile(ini_path):
        parser = configparser.ConfigParser()
        parser.read(ini_path, encoding="utf-8")
        path = parser.get("guispk", "db_path", fallback="").strip()
        if path:
            return os.path.expanduser(path)

    return os.path.expanduser("~/.guispk/guispk.db")
