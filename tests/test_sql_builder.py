"""Tests de generacion de DDL/DML (sin red)."""

import pytest

from core import masking, sql_builder

FIELDS = [
    {"col": "nombre", "type": "string", "masking": masking.MASK_TEXT},
    {"col": "edad", "type": "int", "masking": masking.MASK_INT},
    {"col": "saldo", "type": "decimal(12,2)", "masking": masking.NONE},
]


def test_build_create():
    create = sql_builder.build_create(FIELDS, "proceso_enmascarado.t_enm")
    assert "CREATE TABLE IF NOT EXISTS proceso_enmascarado.t_enm (" in create
    assert "  nombre STRING" in create
    assert "  edad BIGINT" in create
    assert "  saldo decimal(12,2)" in create
    assert create.rstrip().endswith("STORED AS PARQUET;")


def test_build_insert_has_casts_and_aliases():
    insert = sql_builder.build_insert(
        FIELDS, "origen.t", "proceso_enmascarado.t_enm", "saltTexto", 12345
    )
    assert "INSERT INTO proceso_enmascarado.t_enm" in insert
    assert "default.mask_text(nombre, 'saltTexto') AS nombre" in insert
    assert "default.mask_int(cast(edad as bigint), 12345) AS edad" in insert
    assert "saldo AS saldo" in insert
    assert insert.rstrip().endswith("FROM origen.t;")


def test_build_script_concatenates():
    create, insert, script = sql_builder.build_script(
        FIELDS, "origen.t", "proceso_enmascarado.t_enm", "saltTexto", 12345
    )
    assert create in script
    assert insert in script
    assert "-- 1) CREATE" in script
    assert "-- 2) INSERT" in script


def test_build_request_preview_sin_enmascaramiento():
    """El aliado pide columnas: el preview no necesita la clave masking."""
    pedidas = [{"col": "nombre", "type": "string"}, {"col": "edad", "type": "int"}]
    preview = sql_builder.build_request_preview(
        pedidas, "lz.t", "proceso_enmascarado.t_enm", "ingestion_day = 2026-08-01"
    )
    assert "lz.t" in preview
    assert "proceso_enmascarado.t_enm" in preview
    assert "ingestion_day = 2026-08-01" in preview
    assert "  nombre string" in preview
    assert "Columnas solicitadas (2)" in preview


def test_build_request_preview_sin_particion():
    preview = sql_builder.build_request_preview(
        [{"col": "n", "type": "string"}], "lz.t", "d.t", None
    )
    assert "WHERE  : sin particion" in preview


def test_no_fields_raises():
    with pytest.raises(ValueError):
        sql_builder.build_script([], "o.t", "d.t", "s", 1)


def test_missing_text_salt_raises():
    with pytest.raises(ValueError):
        sql_builder.build_script(
            [{"col": "n", "type": "string", "masking": masking.MASK_TEXT}],
            "o.t",
            "d.t",
            "",
            1,
        )


def test_missing_int_salt_raises():
    with pytest.raises(ValueError):
        sql_builder.build_script(
            [{"col": "e", "type": "int", "masking": masking.MASK_INT}],
            "o.t",
            "d.t",
            "salt",
            "",
        )
