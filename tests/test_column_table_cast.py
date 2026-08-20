"""El combo de enmascaramiento y la opcion de casteo (necesita Qt offscreen).

La opcion "entero guardado como texto" es una sola eleccion en la UI pero dos
claves en el campo (`masking` + `cast`): aqui se prueba que el viaje de ida y
vuelta las conserva y que la clave `cast` no aparece cuando no se eligio.
"""

import os

import pytest

pytest.importorskip("PyQt6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import masking  # noqa: E402
from core.ui.column_table import MASK_INT_AS_STRING, ColumnTable  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tabla(qapp):
    return ColumnTable(with_masking=True)


def _combo(tabla, row):
    from core.ui.column_table import COL_MASK

    return tabla.cellWidget(row, COL_MASK)


def _opciones(combo):
    return [combo.itemData(i) for i in range(combo.count())]


def test_la_opcion_de_casteo_solo_existe_en_columnas_de_texto(tabla):
    tabla.load_columns([("num_doc", "string"), ("edad", "int")])
    assert MASK_INT_AS_STRING in _opciones(_combo(tabla, 0))
    assert MASK_INT_AS_STRING not in _opciones(_combo(tabla, 1))


def test_elegir_el_casteo_produce_masking_y_cast(tabla):
    tabla.load_columns([("num_doc", "string")])
    combo = _combo(tabla, 0)
    combo.setCurrentIndex(_opciones(combo).index(MASK_INT_AS_STRING))

    assert tabla.selected_fields() == [
        {
            "col": "num_doc",
            "type": "string",
            "masking": masking.MASK_INT,
            "cast": masking.CAST_BIGINT,
        }
    ]


def test_sin_casteo_no_aparece_la_clave(tabla):
    tabla.load_columns([("nombre", "string"), ("edad", "int")])
    for f in tabla.selected_fields():
        assert "cast" not in f


def test_la_seleccion_guardada_se_restaura(tabla):
    guardado = [
        {
            "col": "num_doc",
            "type": "string",
            "masking": masking.MASK_INT,
            "cast": masking.CAST_BIGINT,
        },
        {"col": "nombre", "type": "string", "masking": masking.MASK_TEXT},
    ]
    tabla.load_fields(guardado)
    assert tabla.selected_fields() == guardado


def test_la_vista_previa_muestra_el_casteo(tabla):
    from core.ui.column_table import COL_PREVIEW

    tabla.load_fields([{
        "col": "num_doc",
        "type": "string",
        "masking": masking.MASK_INT,
        "cast": masking.CAST_BIGINT,
    }])
    assert tabla.item(0, COL_PREVIEW).text().startswith("cast(default.mask_int(")
    assert tabla.item(0, COL_PREVIEW).text().endswith(" as string)")


def test_el_aliado_no_ve_la_opcion_de_casteo(qapp):
    """`selected_columns` ignora el enmascaramiento: la solicitud no lo lleva."""
    tabla = ColumnTable(with_masking=False)
    tabla.load_columns([("num_doc", "string")])
    assert tabla.selected_columns() == [{"col": "num_doc", "type": "string"}]
