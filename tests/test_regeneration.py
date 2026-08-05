"""La verificacion anti-drift: fields_json regenerado == sql_preview guardado."""

from core import masking, models, sql_builder

REQUESTED = [
    {"col": "nombre", "type": "string"},
    {"col": "edad", "type": "int"},
    {"col": "saldo", "type": "decimal(12,2)"},
]
FIELDS = [
    {"col": "nombre", "type": "string", "masking": masking.MASK_TEXT},
    {"col": "edad", "type": "int", "masking": masking.MASK_INT},
    {"col": "saldo", "type": "decimal(12,2)", "masking": masking.NONE},
]
SRC = "lz.clientes"
DEST = "proceso_enmascarado.clientes_enm"
WHERE = "ingestion_day = 2026-08-01"


def test_request_preview_stable():
    """Ciclo del aliado: genera -> guarda -> el interno regenera y compara."""
    preview = sql_builder.build_request_preview(REQUESTED, SRC, DEST, WHERE)
    stored = models.fields_from_json(models.fields_to_json(REQUESTED))
    assert sql_builder.build_request_preview(stored, SRC, DEST, WHERE) == preview


def test_preview_detects_tampering():
    """Cambiar destino o columnas rompe la comparacion con lo guardado."""
    preview = sql_builder.build_request_preview(REQUESTED, SRC, DEST, WHERE)
    otro_destino = sql_builder.build_request_preview(
        REQUESTED, SRC, "proceso_enmascarado.otra", WHERE
    )
    assert otro_destino != preview
    columna_extra = REQUESTED + [{"col": "salario", "type": "double"}]
    assert sql_builder.build_request_preview(columna_extra, SRC, DEST, WHERE) != preview


def test_final_fields_drive_sql():
    """El SQL ejecutado lleva la decision del interno, no lo que pidio el aliado."""
    decision = [
        {"col": "nombre", "type": "string", "masking": masking.MASK_TEXT},
        {"col": "edad", "type": "int", "masking": masking.NONE},
    ]
    _, create, insert, _ = sql_builder.build_request_script(decision, SRC, DEST, WHERE)
    assert "mask_text(nombre" in insert
    assert "mask_int" not in insert  # el interno dejo edad sin enmascarar
    assert "saldo" not in create  # columna excluida por el interno


def test_regenerated_script_matches_preview():
    """Formato viejo: el aliado guardaba el script con placeholders."""
    *_, preview = sql_builder.build_request_script(FIELDS, SRC, DEST, WHERE)
    stored_fields = models.fields_from_json(models.fields_to_json(FIELDS))
    *_, regen = sql_builder.build_request_script(stored_fields, SRC, DEST, WHERE)
    assert regen == preview


def test_placeholders_present_and_substituted():
    _, create, insert, _ = sql_builder.build_request_script(FIELDS, SRC, DEST, WHERE)
    assert masking.TEXT_SALT_PLACEHOLDER in insert
    assert masking.INT_SALT_PLACEHOLDER in insert
    assert masking.TEXT_SALT_PLACEHOLDER not in create  # el CREATE no lleva salts

    final = masking.apply_salts(insert, "saltReal", 999)
    assert masking.TEXT_SALT_PLACEHOLDER not in final
    assert masking.INT_SALT_PLACEHOLDER not in final
    assert "default.mask_text(nombre, 'saltReal')" in final
    assert "default.mask_int(cast(edad as bigint), 999)" in final


def test_tampered_fields_detected():
    *_, preview = sql_builder.build_request_script(FIELDS, SRC, DEST, WHERE)
    tampered = [dict(f) for f in FIELDS]
    tampered[0]["masking"] = masking.NONE  # quitaron el enmascaramiento
    *_, regen = sql_builder.build_request_script(tampered, SRC, DEST, WHERE)
    assert regen != preview


def test_unpartitioned_table_has_no_where():
    _, _, insert, _ = sql_builder.build_request_script(FIELDS, SRC, DEST, None)
    assert "WHERE" not in insert
    assert insert.rstrip().endswith(f"FROM {SRC};")
