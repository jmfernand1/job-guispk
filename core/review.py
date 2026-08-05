"""Reglas de la revision del interno sobre las columnas que pidio el aliado.

El aliado pide columnas (`[{col, type}]`); el interno decide el enmascaramiento
de cada una y puede excluir las que no deban salir (`[{col, type, masking}]`).
Modulo puro: sin UI ni BD, para poder probar la regla de forma aislada.
"""

from core import masking

VALID_MASKINGS = (masking.MASK_TEXT, masking.MASK_INT, masking.NONE)


def is_legacy_item(fields) -> bool:
    """True si los campos vienen del formato viejo (el aliado elegia mascara)."""
    return any("masking" in f for f in fields)


def validate_final_fields(requested, final):
    """Valida la decision del interno contra lo que pidio el aliado.

    El interno solo puede restringir: cambiar el enmascaramiento y dejar
    columnas fuera. Nunca agregar una columna que el aliado no pidio ni
    cambiarle el tipo. Lanza ValueError con el motivo.
    """
    if not final:
        raise ValueError(
            "No queda ninguna columna seleccionada: no hay nada que crear."
        )

    types_by_col = {f["col"]: f["type"] for f in requested}
    seen = set()
    for f in final:
        col = f["col"]
        if col in seen:
            raise ValueError(f"La columna {col} esta repetida en la seleccion.")
        seen.add(col)
        if col not in types_by_col:
            raise ValueError(
                f"La columna {col} no fue solicitada por el aliado. "
                "No se puede agregar al destino."
            )
        if f["type"] != types_by_col[col]:
            raise ValueError(
                f"El tipo de {col} cambio ({types_by_col[col]} -> {f['type']}): "
                "la solicitud fue alterada."
            )
        if f.get("masking") not in VALID_MASKINGS:
            raise ValueError(
                f"Enmascaramiento invalido para {col}: {f.get('masking')!r}."
            )


def excluded_columns(requested, final):
    """Columnas que el aliado pidio y el interno dejo fuera."""
    kept = {f["col"] for f in final}
    return [f["col"] for f in requested if f["col"] not in kept]


def masking_summary(fields) -> str:
    """Resumen legible de la decision, para diálogos y logs."""
    return "\n".join(
        f"  {f['col']}: {masking.LABELS.get(f.get('masking'), f.get('masking'))}"
        for f in fields
    )
