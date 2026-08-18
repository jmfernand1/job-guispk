"""El destino se crea particionado igual que el origen.

En Impala las columnas de particion no se repiten en la lista de columnas
normales del CREATE y llevan tipo. En el INSERT la particion se escribe con los
valores del WHERE (`PARTITION (year=2026, month=8)`) y esas columnas salen del
SELECT; solo cuando el valor no se puede leer del WHERE — o la columna va
enmascarada — se usa el insert dinamico, que las exige al final del SELECT.
"""

import pytest

from core import masking, sql_builder

FIELDS = [
    {"col": "nombre", "type": "string", "masking": masking.MASK_TEXT},
    {"col": "edad", "type": "int", "masking": masking.MASK_INT},
    {"col": "ingestion_year", "type": "int", "masking": masking.NONE},
    {"col": "ingestion_month", "type": "int", "masking": masking.NONE},
]
PART = ["ingestion_year", "ingestion_month"]
DEST = "proceso_enmascarado.t_enm"


def test_create_declara_partitioned_by():
    create = sql_builder.build_create(FIELDS, DEST, PART)
    assert "PARTITIONED BY (\n  ingestion_year int,\n  ingestion_month int\n)" in create
    assert create.rstrip().endswith("STORED AS PARQUET;")


def test_create_no_repite_las_columnas_de_particion():
    """Repetirlas es 'Duplicate column name' en Impala."""
    create = sql_builder.build_create(FIELDS, DEST, PART)
    cuerpo = create.split("PARTITIONED BY")[0]
    assert "ingestion_year" not in cuerpo
    assert "ingestion_month" not in cuerpo
    assert "  nombre STRING" in cuerpo


WHERE_PART = "ingestion_year = 2026 and ingestion_month = 8"


def test_insert_escribe_la_particion_con_los_valores_del_where():
    insert = sql_builder.build_insert(
        FIELDS, "origen.t", DEST, "s", 1, WHERE_PART, PART
    )
    assert (
        f"INSERT INTO {DEST} PARTITION (ingestion_year = 2026, ingestion_month = 8)"
        in insert
    )
    # el WHERE sigue filtrando el origen: se lee y se escribe la misma particion
    assert f"WHERE {WHERE_PART}" in insert


def test_insert_estatico_saca_la_particion_del_select():
    """En un insert estatico Impala no espera esas columnas en el SELECT."""
    insert = sql_builder.build_insert(
        FIELDS, "origen.t", DEST, "s", 1, WHERE_PART, PART
    )
    select = insert.split("FROM")[0]
    alias = [ln.split(" AS ")[-1].strip(" ,") for ln in select.splitlines() if " AS " in ln]
    assert alias == ["nombre", "edad"]


def test_valores_de_particion_se_copian_tal_cual_del_where():
    valores = sql_builder.partition_values("ingestion_day = '2026-08-01' AND h = 3")
    assert valores == {"ingestion_day": "'2026-08-01'", "h": "3"}


def test_insert_dinamico_cuando_el_where_no_da_los_valores():
    """Sin valores no se puede escribir la particion a mano: van al final del SELECT."""
    insert = sql_builder.build_insert(FIELDS, "origen.t", DEST, "s", 1, None, PART)
    assert f"INSERT INTO {DEST} PARTITION (ingestion_year, ingestion_month)" in insert
    cols = [ln.strip() for ln in insert.splitlines() if " AS " in ln]
    assert cols[-2:] == [
        "ingestion_year AS ingestion_year,",
        "ingestion_month AS ingestion_month",
    ]


def test_insert_dinamico_si_la_particion_va_enmascarada():
    """El valor estatico se copia sin mascara: enmascarar exige el SELECT."""
    con_mascara = [
        dict(f, masking=masking.MASK_INT)
        if f["col"] == "ingestion_month"
        else f
        for f in FIELDS
    ]
    insert = sql_builder.build_insert(
        con_mascara, "origen.t", DEST, "s", 1, WHERE_PART, PART
    )
    assert f"INSERT INTO {DEST} PARTITION (ingestion_year, ingestion_month)" in insert
    assert "AS ingestion_month" in insert


def test_respeta_el_orden_de_particion_del_origen():
    """Manda el orden del origen, no el de la seleccion de columnas."""
    invertido = list(reversed(PART))
    create = sql_builder.build_create(FIELDS, DEST, invertido)
    assert create.index("ingestion_month int") < create.index("ingestion_year int")


def test_columna_de_particion_no_pedida_igual_particiona():
    """El destino se particiona como el origen aunque no se pidiera la columna.

    Es una clave de particion, no un dato: no va en el SELECT, su valor lo
    escribe el PARTITION del INSERT. El tipo sale del DESCRIBE del origen.
    """
    sin_mes = [f for f in FIELDS if f["col"] != "ingestion_month"]
    create = sql_builder.build_create(
        sin_mes, DEST, PART, {"ingestion_month": "int"}
    )
    assert "PARTITIONED BY (\n  ingestion_year int,\n  ingestion_month int\n)" in create
    cuerpo = create.split("PARTITIONED BY")[0]
    assert "ingestion_month" not in cuerpo


def test_particion_no_pedida_se_escribe_estatica_en_el_insert():
    sin_mes = [f for f in FIELDS if f["col"] != "ingestion_month"]
    insert = sql_builder.build_insert(
        sin_mes, "origen.t", DEST, "s", 1, WHERE_PART, PART,
        {"ingestion_month": "int"},
    )
    assert (
        f"INSERT INTO {DEST} PARTITION (ingestion_year = 2026, ingestion_month = 8)"
        in insert
    )
    assert "AS ingestion_month" not in insert


def test_tipo_de_particion_sin_describe_sale_del_valor_del_where():
    """Sin tipos, un valor entero es BIGINT y uno con comillas STRING."""
    solo_datos = [f for f in FIELDS if f["col"] not in PART]
    create = sql_builder.build_create(
        solo_datos, DEST, ["ingestion_year", "ingestion_day"],
        filters="ingestion_year = 2026 and ingestion_day = '2026-08-01'",
    )
    assert "PARTITIONED BY (\n  ingestion_year BIGINT,\n  ingestion_day STRING\n)" in create


def test_sin_particion_el_sql_no_cambia():
    """El default es la tabla plana de siempre: solicitudes viejas regeneran igual."""
    antes = sql_builder.build_script(FIELDS, "origen.t", DEST, "s", 1, "a = 1")
    despues = sql_builder.build_script(
        FIELDS, "origen.t", DEST, "s", 1, "a = 1", partition_cols=None
    )
    assert antes == despues
    assert "PARTITIONED BY" not in antes[1]
    assert "PARTITION (" not in antes[2]


def test_partition_cols_fuera_de_la_seleccion_se_sintetizan():
    """Manda SHOW PARTITIONS: la columna entra al PARTITIONED BY igual."""
    create = sql_builder.build_create(FIELDS, DEST, ["ingestion_day"])
    assert "PARTITIONED BY (\n  ingestion_day STRING\n)" in create


def test_todo_particion_falla_claro():
    solo_part = [f for f in FIELDS if f["col"] in PART]
    with pytest.raises(ValueError, match="sin columnas de datos"):
        sql_builder.build_create(solo_part, DEST, PART)


def test_script_completo_documenta_la_particion():
    *_, script = sql_builder.build_script(
        FIELDS, "origen.t", DEST, "s", 1, "ingestion_year = 2026", PART
    )
    assert "-- Particion: ingestion_year, ingestion_month" in script
    # Re-ejecutable: el split de sentencias sigue dando DROP + CREATE + INSERT.
    assert len(sql_builder.split_statements(script)) == 3
