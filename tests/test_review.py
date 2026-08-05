"""Tests de la regla de revision: el interno solo puede restringir."""

import pytest

from core import review

REQUESTED = [
    {"col": "nombre", "type": "string"},
    {"col": "edad", "type": "int"},
    {"col": "ciudad", "type": "string"},
]


def test_decision_valida():
    review.validate_final_fields(
        REQUESTED,
        [
            {"col": "nombre", "type": "string", "masking": "mask_text"},
            {"col": "edad", "type": "int", "masking": "none"},
        ],
    )


def test_columna_no_solicitada_se_rechaza():
    with pytest.raises(ValueError, match="no fue solicitada"):
        review.validate_final_fields(
            REQUESTED,
            [{"col": "salario", "type": "double", "masking": "mask_int"}],
        )


def test_tipo_alterado_se_rechaza():
    with pytest.raises(ValueError, match="tipo de nombre cambio"):
        review.validate_final_fields(
            REQUESTED,
            [{"col": "nombre", "type": "int", "masking": "mask_int"}],
        )


def test_seleccion_vacia_se_rechaza():
    with pytest.raises(ValueError, match="ninguna columna"):
        review.validate_final_fields(REQUESTED, [])


def test_columna_repetida_se_rechaza():
    dup = {"col": "nombre", "type": "string", "masking": "none"}
    with pytest.raises(ValueError, match="repetida"):
        review.validate_final_fields(REQUESTED, [dup, dup])


def test_enmascaramiento_desconocido_se_rechaza():
    with pytest.raises(ValueError, match="Enmascaramiento invalido"):
        review.validate_final_fields(
            REQUESTED,
            [{"col": "nombre", "type": "string", "masking": "borrar_todo"}],
        )


def test_excluded_columns():
    final = [{"col": "nombre", "type": "string", "masking": "mask_text"}]
    assert review.excluded_columns(REQUESTED, final) == ["edad", "ciudad"]


def test_is_legacy_item():
    assert not review.is_legacy_item(REQUESTED)
    assert review.is_legacy_item(
        [{"col": "nombre", "type": "string", "masking": "mask_text"}]
    )
