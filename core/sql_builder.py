"""Construccion del SQL a partir de la seleccion de campos.

`build_script` devuelve (drop, create, insert, script_completo): cada corrida
arranca con DROP ... PURGE, asi que recrear el destino no falla si ya existe.
`build_request_preview` genera el texto de la solicitud del aliado (columnas
pedidas, sin enmascaramiento) y `split_statements` parte un script guardado
para re-ejecutarlo desde el historico.

Sin dependencias de UI ni de red: testeable de forma aislada.
"""

from core import masking


def _validate(fields, src_table, dest_table):
    if not fields:
        raise ValueError("No hay campos seleccionados para enmascarar.")
    if not src_table or not src_table.strip():
        raise ValueError("Falta la tabla origen.")
    if not dest_table or not dest_table.strip():
        raise ValueError("Falta la tabla destino.")


def build_drop(dest_table) -> str:
    """DROP previo al CREATE: permite recrear el destino sin fallar si ya existe.

    PURGE salta la papelera de HDFS: la tabla anterior se borra de inmediato.
    """
    return f"DROP TABLE IF EXISTS {dest_table} PURGE;"


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


def build_insert(fields, src_table, dest_table, text_salt, int_salt, filters=None) -> str:
    """Genera el INSERT INTO ... SELECT ... FROM <origen> con los alias.

    `filters` es la clausula WHERE (sin la palabra WHERE); si viene vacia o None
    la tabla se trata como no particionada y se inserta completa.
    """
    lines = []
    for f in fields:
        expr = masking.select_expr(f["col"], f["masking"], text_salt, int_salt)
        lines.append(f"  {expr} AS {f['col']}")
    select = ",\n".join(lines)
    where = f"\nWHERE {filters.strip()}" if filters and filters.strip() else ""
    return (
        f"INSERT INTO {dest_table}\n"
        f"SELECT\n"
        f"{select}\n"
        f"FROM {src_table}{where};"
    )


def build_script(fields, src_table, dest_table, text_salt, int_salt, filters=None):
    """Devuelve (drop, create, insert, script_completo).

    El script completo concatena las tres sentencias con una cabecera. El DROP
    va primero para poder recrear el destino: cada ejecucion deja la tabla
    destino con exactamente el contenido de esta corrida.
    """
    _validate(fields, src_table, dest_table)
    if not text_salt and any(f["masking"] == masking.MASK_TEXT for f in fields):
        raise ValueError("Falta el salt de texto (hay columnas mask_text).")
    if int_salt in (None, "") and any(
        f["masking"] == masking.MASK_INT for f in fields
    ):
        raise ValueError("Falta el salt entero (hay columnas mask_int).")

    drop = build_drop(dest_table)
    create = build_create(fields, dest_table)
    insert = build_insert(fields, src_table, dest_table, text_salt, int_salt, filters)
    script = (
        f"-- Script de enmascaramiento generado automaticamente\n"
        f"-- Origen : {src_table}\n"
        f"-- Destino: {dest_table}\n\n"
        f"-- 1) DROP (el destino se recrea desde cero)\n"
        f"{drop}\n\n"
        f"-- 2) CREATE\n"
        f"{create}\n\n"
        f"-- 3) INSERT\n"
        f"{insert}\n"
    )
    return drop, create, insert, script


def split_statements(script: str):
    """Parte un script guardado en sentencias ejecutables, sin comentarios.

    Se usa para re-ejecutar un script del historico. El corte se hace sobre el
    texto con placeholders (antes de sustituir los salts), asi un salt con ';'
    no puede partir una sentencia.
    """
    sin_comentarios = "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("--")
    )
    return [s.strip() + ";" for s in sin_comentarios.split(";") if s.strip()]


def build_request_preview(fields, src_table, dest_table, filters=None) -> str:
    """Texto de la solicitud del aliado: origen, destino, WHERE y columnas.

    El aliado ya no elige enmascaramiento, asi que no puede generar SQL; pide
    columnas. Este texto es lo que se guarda en sql_preview y lo que el interno
    regenera antes de ejecutar para detectar cualquier alteracion de la
    solicitud (por eso incluye origen, destino y WHERE, no solo las columnas).
    """
    _validate(fields, src_table, dest_table)
    cols = "\n".join(f"  {f['col']} {f['type']}" for f in fields)
    where = filters.strip() if filters and filters.strip() else "sin particion"
    return (
        f"-- Solicitud de enmascaramiento\n"
        f"-- Origen : {src_table}\n"
        f"-- Destino: {dest_table}\n"
        f"-- WHERE  : {where}\n"
        f"-- El equipo interno define el enmascaramiento de cada columna.\n\n"
        f"Columnas solicitadas ({len(fields)}):\n"
        f"{cols}\n"
    )


def build_request_script(fields, src_table, dest_table, filters=None):
    """Script con salts placeholder: es el SQL final que arma el interno.

    Los salts reales se sustituyen con masking.apply_salts justo antes de
    ejecutar, para que nunca queden escritos en la BD compartida.
    """
    return build_script(
        fields,
        src_table,
        dest_table,
        masking.TEXT_SALT_PLACEHOLDER,
        masking.INT_SALT_PLACEHOLDER,
        filters,
    )
