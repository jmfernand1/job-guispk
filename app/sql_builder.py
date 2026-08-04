"""Construccion de CREATE TABLE + INSERT a partir de la seleccion de campos.

Sin dependencias de UI ni de red: testeable de forma aislada.
"""

from app import masking


def _validate(fields, src_table, dest_table):
    if not fields:
        raise ValueError("No hay campos seleccionados para enmascarar.")
    if not src_table or not src_table.strip():
        raise ValueError("Falta la tabla origen.")
    if not dest_table or not dest_table.strip():
        raise ValueError("Falta la tabla destino.")


def build_create(fields, dest_table) -> str:
    """Genera el CREATE TABLE IF NOT EXISTS ... STORED AS PARQUET."""
    lines = []
    for f in fields:
        dtype = masking.dest_type(f["type"], f["masking"])
        lines.append(f"  {f['col']} {dtype}")
    cols = ",\n".join(lines)
    return (
        f"CREATE TABLE IF NOT EXISTS {dest_table} (\n"
        f"{cols}\n"
        f") STORED AS PARQUET;"
    )


def build_insert(fields, src_table, dest_table, text_salt, int_salt, filters) -> str:
    """Genera el INSERT INTO ... SELECT ... FROM <origen> con los alias."""
    lines = []
    for f in fields:
        expr = masking.select_expr(f["col"], f["masking"], text_salt, int_salt)
        lines.append(f"  {expr} AS {f['col']}")
    select = ",\n".join(lines)
    return (
        f"INSERT INTO {dest_table}\n"
        f"SELECT\n"
        f"{select}\n"
        f"FROM {src_table}\n"
        f"WHERE {filters};"
    )


def build_script(fields, src_table, dest_table, text_salt, int_salt, filters):
    """Devuelve (create, insert, script_completo).

    El script completo concatena ambos con un comentario de cabecera.
    """
    _validate(fields, src_table, dest_table)
    if not text_salt and any(f["masking"] == masking.MASK_TEXT for f in fields):
        raise ValueError("Falta el salt de texto (hay columnas mask_text).")
    if int_salt in (None, "") and any(
        f["masking"] == masking.MASK_INT for f in fields
    ):
        raise ValueError("Falta el salt entero (hay columnas mask_int).")

    create = build_create(fields, dest_table)
    insert = build_insert(fields, src_table, dest_table, text_salt, int_salt, filters)
    script = (
        f"-- Script de enmascaramiento generado automaticamente\n"
        f"-- Origen : {src_table}\n"
        f"-- Destino: {dest_table}\n\n"
        f"-- 1) CREATE\n"
        f"{create}\n\n"
        f"-- 2) INSERT\n"
        f"{insert}\n"
    )
    return create, insert, script
