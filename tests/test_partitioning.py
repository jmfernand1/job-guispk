"""El destino se crea particionado igual que el origen.

En Impala las columnas de particion no se repiten en la lista de columnas
normales, llevan tipo, y en el INSERT dinamico tienen que ir al final del
SELECT. Estos tests fijan esas tres reglas.
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


def test_insert_dinamico_con_particion_al_final():
    insert = sql_builder.build_insert(FIELDS, "origen.t", DEST, "s", 1, None, PART)
    assert f"INSERT INTO {DEST} PARTITION (ingestion_year, ingestion_month)" in insert
    cols = [ln.strip() for ln in insert.splitlines() if " AS " in ln]
    assert cols[-2:] == [
        "ingestion_year AS ingestion_year,",
        "ingestion_month AS ingestion_month",
    ]


def test_respeta_el_orden_de_particion_del_origen():
    """Manda el orden del origen, no el de la seleccion de columnas."""
    invertido = list(reversed(PART))
    create = sql_builder.build_create(FIELDS, DEST, invertido)
    assert create.index("ingestion_month int") < create.index("ingestion_year int")


def test_columna_de_particion_excluida_no_particiona():
    """Si el interno no la deja salir, no puede ser clave del destino."""
    sin_mes = [f for f in FIELDS if f["col"] != "ingestion_month"]
    create = sql_builder.build_create(sin_mes, DEST, PART)
    assert "PARTITIONED BY (\n  ingestion_year int\n)" in create
    assert "ingestion_month" not in create


def test_sin_particion_el_sql_no_cambia():
    """El default es la tabla plana de siempre: solicitudes viejas regeneran igual."""
    antes = sql_builder.build_script(FIELDS, "origen.t", DEST, "s", 1, "a = 1")
    despues = sql_builder.build_script(
        FIELDS, "origen.t", DEST, "s", 1, "a = 1", partition_cols=None
    )
    assert antes == despues
    assert "PARTITIONED BY" not in antes[1]
    assert "PARTITION (" not in antes[2]


def test_partition_cols_desconocidas_se_ignoran():
    """Una columna de particion que no esta entre los campos no rompe nada."""
    create = sql_builder.build_create(FIELDS, DEST, ["no_existe"])
    assert "PARTITIONED BY" not in create


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
