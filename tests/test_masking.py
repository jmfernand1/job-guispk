"""Tests del mapeo de tipos y constructores de expresiones (sin red)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import masking


def test_base_type_strips_params():
    assert masking.base_type("varchar(50)") == "varchar"
    assert masking.base_type("  STRING ") == "string"
    assert masking.base_type("DECIMAL(12,2)") == "decimal"


def test_suggest_string_types():
    assert masking.suggest_masking("string") == masking.MASK_TEXT
    assert masking.suggest_masking("varchar(50)") == masking.MASK_TEXT
    assert masking.suggest_masking("char(3)") == masking.MASK_TEXT


def test_suggest_int_types():
    for t in ["tinyint", "smallint", "int", "bigint"]:
        assert masking.suggest_masking(t) == masking.MASK_INT


def test_suggest_none_types():
    for t in ["decimal(12,2)", "double", "timestamp", "date", "boolean"]:
        assert masking.suggest_masking(t) == masking.NONE


def test_select_expr_text():
    assert (
        masking.select_expr("nombre", masking.MASK_TEXT, "salt1", 99)
        == "default.mask_text(nombre, 'salt1')"
    )


def test_select_expr_int():
    assert (
        masking.select_expr("edad", masking.MASK_INT, "salt1", 12345)
        == "default.mask_int(cast(edad as bigint), 12345)"
    )


def test_select_expr_none_passthrough():
    assert masking.select_expr("saldo", masking.NONE, "s", 1) == "saldo"


def test_dest_type():
    assert masking.dest_type("varchar(50)", masking.MASK_TEXT) == "STRING"
    assert masking.dest_type("int", masking.MASK_INT) == "BIGINT"
    assert masking.dest_type("decimal(12,2)", masking.NONE) == "decimal(12,2)"
