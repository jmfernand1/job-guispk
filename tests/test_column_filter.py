"""El filtro del buscador de columnas: que encuentra y que no.

Solo prueba la funcion de coincidencia (sin Qt): es la que decide la
experiencia de escribir y ver la lista reducirse.
"""

import pytest

pytest.importorskip("PyQt6")

from core.ui.column_table import matches, normalize  # noqa: E402


def test_normaliza_acentos_y_mayusculas():
    assert normalize("Año_Crédito") == "ano_credito"


def test_vacio_deja_pasar_todo():
    assert matches("", "num_cliente")
    assert matches("   ", "num_cliente")


def test_subcadena_en_cualquier_posicion():
    assert matches("cli", "num_cliente")
    assert matches("NUM", "num_cliente")
    assert matches("ano", "año_proceso")


def test_varias_palabras_en_cualquier_orden():
    assert matches("cliente num", "num_cliente")
    assert not matches("cliente saldo", "num_cliente")


def test_subsecuencia_para_nombres_a_medias():
    assert matches("nclte", "num_cliente")
    assert not matches("xyz", "num_cliente")


def test_no_confunde_columnas_distintas():
    assert not matches("saldo", "num_cliente")
