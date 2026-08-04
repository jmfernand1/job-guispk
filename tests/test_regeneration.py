"""La verificacion anti-drift: fields_json regenerado == sql_preview guardado."""

from core import masking, models, sql_builder

FIELDS = [
    {"col": "nombre", "type": "string", "masking": masking.MASK_TEXT},
    {"col": "edad", "type": "int", "masking": masking.MASK_INT},
    {"col": "saldo", "type": "decimal(12,2)", "masking": masking.NONE},
]
SRC = "lz.clientes"
DEST = "proceso_enmascarado.clientes_enm"
WHERE = "ingestion_day = 2026-08-01"


def test_regenerated_script_matches_preview():
    """Simula el ciclo completo: aliado genera -> guarda -> interno regenera."""
    _, _, preview = sql_builder.build_request_script(FIELDS, SRC, DEST, WHERE)
    stored_fields = models.fields_from_json(models.fields_to_json(FIELDS))
    _, _, regen = sql_builder.build_request_script(stored_fields, SRC, DEST, WHERE)
    assert regen == preview


def test_placeholders_present_and_substituted():
    create, insert, _ = sql_builder.build_request_script(FIELDS, SRC, DEST, WHERE)
    assert masking.TEXT_SALT_PLACEHOLDER in insert
    assert masking.INT_SALT_PLACEHOLDER in insert
    assert masking.TEXT_SALT_PLACEHOLDER not in create  # el CREATE no lleva salts

    final = masking.apply_salts(insert, "saltReal", 999)
    assert masking.TEXT_SALT_PLACEHOLDER not in final
    assert masking.INT_SALT_PLACEHOLDER not in final
    assert "default.mask_text(nombre, 'saltReal')" in final
    assert "default.mask_int(cast(edad as bigint), 999)" in final


def test_tampered_fields_detected():
    _, _, preview = sql_builder.build_request_script(FIELDS, SRC, DEST, WHERE)
    tampered = [dict(f) for f in FIELDS]
    tampered[0]["masking"] = masking.NONE  # quitaron el enmascaramiento
    _, _, regen = sql_builder.build_request_script(tampered, SRC, DEST, WHERE)
    assert regen != preview


def test_unpartitioned_table_has_no_where():
    _, insert, _ = sql_builder.build_request_script(FIELDS, SRC, DEST, None)
    assert "WHERE" not in insert
    assert insert.rstrip().endswith(f"FROM {SRC};")
