"""Salts reales del proyecto: SOLO en la maquina del equipo interno.

Viven en ~/.guispk/salts.json (fuera de la carpeta compartida) porque la BD
compartida es legible por los aliados y un salt conocido permite ataques de
diccionario sobre los valores enmascarados.

Formato:
{
  "label": "proyecto-x-2026",
  "text_salt": "cadenaSecreta",
  "int_salt": 987654321
}

Un mismo par de salts por proyecto preserva la integridad referencial entre
tablas (mismo valor origen -> mismo valor enmascarado).
"""

import json
import os

SALTS_PATH = os.path.expanduser("~/.guispk/salts.json")

_EXAMPLE = (
    '{"label": "proyecto-x", "text_salt": "<secreto>", "int_salt": 123456789}'
)


def load_salts(path=None) -> dict:
    """Carga y valida el archivo de salts. Lanza error claro si falta algo."""
    path = path or SALTS_PATH
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"No existe el archivo de salts: {path}\n"
            f"Crealo (fuera de la carpeta compartida) con el formato:\n{_EXAMPLE}"
        )
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for key in ("label", "text_salt", "int_salt"):
        if key not in data or data[key] in (None, ""):
            raise ValueError(f"El archivo de salts {path} no tiene la clave '{key}'.")
    return data
