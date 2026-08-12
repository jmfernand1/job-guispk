"""Construccion del SQL a partir de la seleccion de campos.

`build_script` devuelve (drop, create, insert, script_completo): cada corrida
arranca con DROP ... PURGE, asi que recrear el destino no falla si ya existe.
`build_request_preview` genera el texto de la solicitud del aliado (columnas
pedidas, sin enmascaramiento), `build_export_script` junta los scripts de una
solicitud en un archivo editable y `split_statements` parte un script guardado
para re-ejecutarlo desde el historico.

Sin dependencias de UI ni de red: testeable de forma aislada.
"""

import re

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


def split_partition_fields(fields, partition_cols=None):
    """Parte los campos en (datos, particion), respetando el orden del origen.

    `partition_cols` son los nombres de las columnas por las que particiona la
    tabla origen (los que devuelve `SparkyClient.get_partition_info`). Solo se
    particiona por las que el interno dejo pasar: una columna que no viaja al
    destino no puede ser su clave de particion.
    """
    selected = {f["col"]: f for f in fields}
    part = [selected[c] for c in (partition_cols or []) if c in selected]
    part_names = {f["col"] for f in part}
    data = [f for f in fields if f["col"] not in part_names]
    return data, part


def partition_values(filters):
    """Devuelve `{columna: valor}` leyendo el WHERE de la particion.

    El WHERE lo arma `SparkyClient.get_partition_info` como
    `"col = valor and col2 = valor2"`. El valor se copia **tal cual**, sin
    reinterpretarlo ni citarlo: es el mismo texto que filtra el origen, asi que
    el INSERT escribe en la particion que lee.

    Los terminos que no sean una igualdad simple se ignoran; el que los pida
    (`build_insert`) se da cuenta porque falta la columna y cae al insert
    dinamico.
    """
    valores = {}
    for termino in re.split(r"\s+AND\s+", (filters or "").strip(), flags=re.I):
        col, sep, val = termino.partition("=")
        if sep and col.strip() and val.strip():
            valores[col.strip()] = val.strip()
    return valores


def build_create(fields, dest_table, partition_cols=None) -> str:
    """Genera el CREATE TABLE IF NOT EXISTS ... STORED AS PARQUET.

    Si el origen esta particionado, el destino se crea con la misma clave via
    PARTITIONED BY. Ojo: en Impala las columnas de particion **no** se repiten
    en la lista de columnas normales y llevan su tipo, no solo el nombre.
    """
    data, part = split_partition_fields(fields, partition_cols)
    if not data:
        raise ValueError(
            "Todas las columnas seleccionadas son de particion: la tabla "
            "destino quedaria sin columnas de datos."
        )

    def _decl(f):
        return f"  {f['col']} {masking.dest_type(f['type'], f['masking'])}"

    cols = ",\n".join(_decl(f) for f in data)
    partitioned = ""
    if part:
        partitioned = (
            "PARTITIONED BY (\n" + ",\n".join(_decl(f) for f in part) + "\n) "
        )
    return (
        f"CREATE TABLE IF NOT EXISTS {dest_table} (\n"
        f"{cols}\n"
        f") {partitioned}STORED AS PARQUET;"
    )


def build_insert(
    fields, src_table, dest_table, text_salt, int_salt, filters=None,
    partition_cols=None,
) -> str:
    """Genera el INSERT INTO ... SELECT ... FROM <origen> con los alias.

    `filters` es la clausula WHERE (sin la palabra WHERE); si viene vacia o None
    la tabla se trata como no particionada y se inserta completa.

    Con destino particionado el insert es **estatico**: los valores salen del
    mismo WHERE que filtra el origen
    (`INSERT INTO d PARTITION (year=2026, month=8) SELECT <solo datos> ...`), asi
    que las columnas de particion **no van en el SELECT** — en un insert estatico
    Impala no las espera ahi y sobrarian contra la lista de columnas del destino.

    Se cae al insert dinamico de siempre (`PARTITION (year, month)` con esas
    columnas al final del SELECT, que es donde Impala las exige) en dos casos: si
    el WHERE no da valor para alguna columna de particion, y si alguna esta
    enmascarada — el valor estatico se copia del origen sin pasar por la mascara,
    asi que enmascararla exige que la escriba el SELECT.
    """
    data, part = split_partition_fields(fields, partition_cols)
    valores = partition_values(filters)
    estatica = bool(part) and all(
        f["col"] in valores and f["masking"] == masking.NONE for f in part
    )
    lines = [
        f"  {masking.select_expr(f['col'], f['masking'], text_salt, int_salt)} "
        f"AS {f['col']}"
        for f in (data if estatica else data + part)
    ]
    select = ",\n".join(lines)
    where = f"\nWHERE {filters.strip()}" if filters and filters.strip() else ""
    if estatica:
        asignaciones = ", ".join(f"{f['col']}={valores[f['col']]}" for f in part)
        partition = f" PARTITION ({asignaciones})"
    else:
        partition = (
            f" PARTITION ({', '.join(f['col'] for f in part)})" if part else ""
        )
    return (
        f"INSERT INTO {dest_table}{partition}\n"
        f"SELECT\n"
        f"{select}\n"
        f"FROM {src_table}{where};"
    )


def build_script(
    fields, src_table, dest_table, text_salt, int_salt, filters=None,
    partition_cols=None,
):
    """Devuelve (drop, create, insert, script_completo).

    El script completo concatena las tres sentencias con una cabecera. El DROP
    va primero para poder recrear el destino: cada ejecucion deja la tabla
    destino con exactamente el contenido de esta corrida.

    `partition_cols` (nombres de las columnas de particion del origen) hace que
    el destino se cree particionado igual. Sin ellas el comportamiento es el de
    siempre: tabla plana. Ese default importa — la verificacion de solicitudes
    viejas regenera su script sin particion y tiene que dar identico.
    """
    _validate(fields, src_table, dest_table)
    if not text_salt and any(f["masking"] == masking.MASK_TEXT for f in fields):
        raise ValueError("Falta el salt de texto (hay columnas mask_text).")
    if int_salt in (None, "") and any(
        f["masking"] == masking.MASK_INT for f in fields
    ):
        raise ValueError("Falta el salt entero (hay columnas mask_int).")

    drop = build_drop(dest_table)
    create = build_create(fields, dest_table, partition_cols)
    insert = build_insert(
        fields, src_table, dest_table, text_salt, int_salt, filters,
        partition_cols,
    )
    _, part = split_partition_fields(fields, partition_cols)
    particion = (
        f"-- Particion: {', '.join(f['col'] for f in part)}\n" if part else ""
    )
    script = (
        f"-- Script de enmascaramiento generado automaticamente\n"
        f"-- Origen : {src_table}\n"
        f"-- Destino: {dest_table}\n"
        f"{particion}\n"
        f"-- 1) DROP (el destino se recrea desde cero)\n"
        f"{drop}\n\n"
        f"-- 2) CREATE\n"
        f"{create}\n\n"
        f"-- 3) INSERT\n"
        f"{insert}\n"
    )
    return drop, create, insert, script


def build_export_script(entries, header_lines=None) -> str:
    """Une los scripts de varias tablas en un solo .sql exportable.

    `entries` es [(titulo, script)] — un par por tabla. Lo usa el interno para
    llevarse el SQL de una solicitud completa a un archivo y editarlo a mano
    cuando la corrida necesita alguna variante que la app no ofrece.

    `header_lines` sale como comentarios al principio: de ahi cuelgan la
    solicitud, el salt usado y el aviso de que el archivo no se comparte.
    """
    partes = []
    if header_lines:
        partes.append("\n".join(f"-- {line}" for line in header_lines))
    for titulo, script in entries:
        partes.append(f"-- ===== {titulo} =====\n{script.rstrip()}")
    return "\n\n".join(partes) + "\n"


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


def build_request_script(
    fields, src_table, dest_table, filters=None, partition_cols=None
):
    """Script con salts placeholder: es el SQL final que arma el interno.

    Los salts reales se sustituyen con masking.apply_salts justo antes de
    ejecutar, para que nunca queden escritos en la BD compartida.

    `partition_cols` se omite al regenerar solicitudes en formato viejo: ese
    script se compara contra el guardado y tiene que salir igual.
    """
    return build_script(
        fields,
        src_table,
        dest_table,
        masking.TEXT_SALT_PLACEHOLDER,
        masking.INT_SALT_PLACEHOLDER,
        filters,
        partition_cols,
    )
