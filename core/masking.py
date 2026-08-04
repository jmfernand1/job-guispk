"""Reglas de enmascaramiento: mapeo tipo Impala -> funcion y constructores de SQL.

Sin dependencias de UI ni de red: testeable de forma aislada.

Funciones disponibles en la Landing Zone (default):
- default.mask_text(campo_texto, 'salt_texto')        -> STRING
- default.mask_int(cast(campo as bigint), salt_entero) -> BIGINT
"""

STRING_TYPES = {"string", "varchar", "char"}
INT_TYPES = {"tinyint", "smallint", "int", "bigint"}

MASK_TEXT = "mask_text"
MASK_INT = "mask_int"
NONE = "none"

# Etiquetas legibles para el combo de la UI.
LABELS = {
    MASK_TEXT: "mask_text (texto)",
    MASK_INT: "mask_int (entero)",
    NONE: "Sin enmascarar",
}

# Placeholders de salts: el aliado genera SQL con estos tokens y el equipo
# interno los sustituye por los salts reales justo antes de ejecutar.
# Los salts reales NUNCA se guardan en la BD compartida.
TEXT_SALT_PLACEHOLDER = "{{TEXT_SALT}}"
INT_SALT_PLACEHOLDER = "{{INT_SALT}}"


def base_type(impala_type: str) -> str:
    """Normaliza un tipo Impala a su forma base.

    "varchar(50)" -> "varchar", "  STRING " -> "string".
    """
    if impala_type is None:
        return ""
    return impala_type.lower().split("(")[0].strip()


def suggest_masking(impala_type: str) -> str:
    """Sugiere la funcion de enmascaramiento segun el tipo de la columna."""
    b = base_type(impala_type)
    if b in STRING_TYPES:
        return MASK_TEXT
    if b in INT_TYPES:
        return MASK_INT
    return NONE  # decimal / double / timestamp / date / boolean -> sin funcion


def select_expr(col: str, masking: str, text_salt: str, int_salt) -> str:
    """Construye la expresion SELECT para una columna segun su enmascaramiento."""
    if masking == MASK_TEXT:
        return f"default.mask_text({col}, '{text_salt}')"
    if masking == MASK_INT:
        return f"default.mask_int(cast({col} as bigint), {int_salt})"
    return col


def dest_type(orig_type: str, masking: str) -> str:
    """Tipo de la columna en la tabla destino segun el enmascaramiento aplicado."""
    if masking == MASK_TEXT:
        return "STRING"
    if masking == MASK_INT:
        return "BIGINT"
    return orig_type


def apply_salts(sql: str, text_salt: str, int_salt) -> str:
    """Sustituye los placeholders de salt por los valores reales (lado interno)."""
    return sql.replace(TEXT_SALT_PLACEHOLDER, str(text_salt)).replace(
        INT_SALT_PLACEHOLDER, str(int_salt)
    )
