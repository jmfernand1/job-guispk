"""Casteo de enteros guardados como texto: expresion, tipo destino y regla.

Hay columnas que el DESCRIBE declara string pero que guardan enteros. Al
enmascararlas con mask_int hay que castear de ida (para la mascara) y de vuelta
(para que el destino siga siendo string y el INSERT no falle). La clave `cast`
del campo es opcional: sin ella, todo tiene que salir exactamente igual que
antes de que existiera — de eso depende la verificacion de solicitudes viejas.
"""

import pytest

from core import masking, review, sql_builder

REQUESTED = [
    {"col": "num_doc", "type": "string"},
    {"col": "nombre", "type": "string"},
    {"col": "edad", "type": "int"},
]

CASTEADO = {
    "col": "num_doc",
    "type": "string",
    "masking": masking.MASK_INT,
    "cast": masking.CAST_BIGINT,
}


# -- expresion y tipo destino ---------------------------------------------
def test_select_expr_castea_de_ida_y_vuelta():
    assert masking.select_expr(
        "num_doc", masking.MASK_INT, "salt1", 99, masking.CAST_BIGINT
    ) == "cast(default.mask_int(cast(num_doc as bigint), 99) as string)"


def test_select_expr_sin_cast_no_cambia():
    assert masking.select_expr(
        "edad", masking.MASK_INT, "salt1", 99
    ) == "default.mask_int(cast(edad as bigint), 99)"


def test_cast_no_afecta_a_mask_text_ni_a_none():
    assert masking.select_expr(
        "nombre", masking.MASK_TEXT, "salt1", 99, masking.CAST_BIGINT
    ) == "default.mask_text(nombre, 'salt1')"
    assert masking.select_expr(
        "nombre", masking.NONE, "salt1", 99, masking.CAST_BIGINT
    ) == "nombre"


def test_dest_type_casteado_es_string():
    assert masking.dest_type("string", masking.MASK_INT, masking.CAST_BIGINT) == "STRING"
    assert masking.dest_type("string", masking.MASK_INT) == "BIGINT"


# -- SQL generado ----------------------------------------------------------
def test_script_casteado_crea_string_e_inserta_la_expresion():
    fields = [CASTEADO, {"col": "edad", "type": "int", "masking": masking.MASK_INT}]
    _, create, insert, _ = sql_builder.build_script(
        fields, "origen.t", "proceso_enmascarado.t_enm", "saltTexto", 12345
    )
    assert "  num_doc STRING" in create
    assert "  edad BIGINT" in create
    assert (
        "cast(default.mask_int(cast(num_doc as bigint), 12345) as string) AS num_doc"
        in insert
    )


def test_sin_cast_el_sql_es_identico_al_de_siempre():
    """Regresion: la clave ausente no puede mover ni un byte del script."""
    fields = [
        {"col": "nombre", "type": "string", "masking": masking.MASK_TEXT},
        {"col": "edad", "type": "int", "masking": masking.MASK_INT},
    ]
    esperado = sql_builder.build_script(
        fields, "origen.t", "proceso_enmascarado.t_enm", "saltTexto", 12345
    )
    con_clave_nula = [dict(f, cast=None) for f in fields]
    assert (
        sql_builder.build_script(
            con_clave_nula, "origen.t", "proceso_enmascarado.t_enm", "saltTexto", 12345
        )
        == esperado
    )


def test_columna_de_particion_casteada_va_al_insert_dinamico():
    """Casteada = enmascarada: el valor estatico no pasaria por la mascara."""
    fields = [
        {"col": "nombre", "type": "string", "masking": masking.MASK_TEXT},
        {
            "col": "anio",
            "type": "string",
            "masking": masking.MASK_INT,
            "cast": masking.CAST_BIGINT,
        },
    ]
    insert = sql_builder.build_insert(
        fields, "origen.t", "proceso_enmascarado.t_enm", "saltTexto", 12345,
        filters="anio = 2026", partition_cols=["anio"],
    )
    assert "PARTITION (anio)" in insert
    assert "as string) AS anio" in insert


# -- regla de revision -----------------------------------------------------
def test_decision_con_cast_valida():
    review.validate_final_fields(REQUESTED, [CASTEADO])


def test_cast_desconocido_se_rechaza():
    with pytest.raises(ValueError, match="Casteo invalido"):
        review.validate_final_fields(
            REQUESTED, [dict(CASTEADO, cast="entero")]
        )


def test_cast_sin_mask_int_se_rechaza():
    with pytest.raises(ValueError, match="exige enmascaramiento mask_int"):
        review.validate_final_fields(
            REQUESTED, [dict(CASTEADO, masking=masking.MASK_TEXT)]
        )


def test_cast_sobre_columna_no_textual_se_rechaza():
    with pytest.raises(ValueError, match="solo aplica a columnas de texto"):
        review.validate_final_fields(
            REQUESTED,
            [{
                "col": "edad",
                "type": "int",
                "masking": masking.MASK_INT,
                "cast": masking.CAST_BIGINT,
            }],
        )
