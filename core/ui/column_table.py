"""QTableWidget para seleccionar campos y su enmascaramiento."""

import unicodedata

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from core import masking

COL_USE = 0
COL_NAME = 1
COL_TYPE = 2
COL_MASK = 3
COL_PREVIEW = 4

_MASK_ORDER = [masking.MASK_TEXT, masking.MASK_INT, masking.NONE]

# Cuarta opcion del combo, solo para columnas de texto: mask_int con casteo de
# ida y vuelta. No es una mascara distinta, es (mask_int + cast); el combo la
# ofrece como una sola eleccion porque para quien revisa es una sola decision.
MASK_INT_AS_STRING = "mask_int_as_string"
_MASK_INT_AS_STRING_LABEL = "mask_int (entero guardado como texto)"


def _combo_key(mask, cast):
    """Opcion del combo que representa a un campo guardado."""
    if mask == masking.MASK_INT and cast == masking.CAST_BIGINT:
        return MASK_INT_AS_STRING
    return mask


def normalize(text):
    """Minusculas y sin acentos: 'Año_Créd' -> 'ano_cred'."""
    decomposed = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def matches(needle, name):
    """True si `name` se parece a lo tecleado en `needle`.

    Tres formas de coincidir, de la mas estricta a la mas laxa:
    subcadena ('cli' -> 'num_cliente'), todos los trozos separados por espacio
    ('num cli' -> 'num_cliente') y subsecuencia ('nclte' -> 'num_cliente'),
    para que el filtro siga sirviendo con nombres a medio recordar.
    """
    needle, name = normalize(needle).strip(), normalize(name)
    if not needle:
        return True
    if needle in name:
        return True
    parts = needle.split()
    if len(parts) > 1 and all(p in name for p in parts):
        return True
    pos = 0
    for ch in needle.replace(" ", ""):
        pos = name.find(ch, pos) + 1
        if pos == 0:
            return False
    return True


class ColumnTable(QTableWidget):
    """Una fila por columna: [Usar] | nombre | tipo | [combo] | preview.

    Con `with_masking=False` (modo aliado) se ocultan las columnas de
    enmascaramiento y vista previa: el aliado solo elige que columnas necesita
    y el enmascaramiento lo decide el interno.
    """

    #: (visibles, total) cada vez que cambia el filtro o se recargan columnas
    filterChanged = pyqtSignal(int, int)

    def __init__(self, parent=None, with_masking=True):
        super().__init__(0, 5, parent)
        self.setHorizontalHeaderLabels(
            ["Usar", "Columna", "Tipo origen", "Enmascaramiento", "Vista previa"]
        )
        self.horizontalHeader().setStretchLastSection(True)
        self.setColumnWidth(COL_USE, 50)
        self.setColumnWidth(COL_NAME, 180)
        self.setColumnWidth(COL_TYPE, 120)
        self.setColumnWidth(COL_MASK, 160)
        self._with_masking = with_masking
        if not with_masking:
            self.setColumnHidden(COL_MASK, True)
            self.setColumnHidden(COL_PREVIEW, True)
        # salts actuales para la vista previa
        self._text_salt = "saltTexto"
        self._int_salt = "12345"
        self._filter = ""

    # -- carga --------------------------------------------------------------
    def load_columns(self, columns):
        """columns: lista de (name, type). Llena la tabla y autoselecciona."""
        self.setRowCount(0)
        for name, ctype in columns:
            self._add_row(name, ctype)
        self._apply_filter()

    def load_fields(self, fields):
        """fields: [{col, type, masking?, cast?}]. Restaura una seleccion guardada.

        Si el campo trae `masking` se preselecciona en el combo; si no, queda
        la sugerencia automatica por tipo.
        """
        self.setRowCount(0)
        for f in fields:
            self._add_row(
                f["col"], f["type"], _combo_key(f.get("masking"), f.get("cast"))
            )
        self._apply_filter()

    def _add_row(self, name, ctype, mask=None):
        row = self.rowCount()
        self.insertRow(row)

        # checkbox "Usar", centrado
        chk = QCheckBox()
        chk.setChecked(True)
        chk.stateChanged.connect(self._refresh_row_factory(row))
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.addWidget(chk)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setContentsMargins(0, 0, 0, 0)
        self.setCellWidget(row, COL_USE, wrap)

        name_item = QTableWidgetItem(name)
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.setItem(row, COL_NAME, name_item)

        type_item = QTableWidgetItem(ctype)
        type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.setItem(row, COL_TYPE, type_item)

        combo = QComboBox()
        opciones = list(_MASK_ORDER)
        for key in opciones:
            combo.addItem(masking.LABELS[key], key)
        if masking.base_type(ctype) in masking.STRING_TYPES:
            combo.addItem(_MASK_INT_AS_STRING_LABEL, MASK_INT_AS_STRING)
            opciones.append(MASK_INT_AS_STRING)
        if mask not in opciones:
            mask = masking.suggest_masking(ctype)
        combo.setCurrentIndex(opciones.index(mask))
        combo.currentIndexChanged.connect(self._refresh_row_factory(row))
        self.setCellWidget(row, COL_MASK, combo)

        preview_item = QTableWidgetItem("")
        preview_item.setFlags(preview_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.setItem(row, COL_PREVIEW, preview_item)

        self._refresh_row(row)

    # -- preview ------------------------------------------------------------
    def _refresh_row_factory(self, row):
        return lambda *_: self._refresh_row(row)

    def _refresh_row(self, row):
        name = self.item(row, COL_NAME).text()
        mask, cast = self._row_masking(row)
        expr = masking.select_expr(
            name, mask, self._text_salt, self._int_salt, cast
        )
        self.item(row, COL_PREVIEW).setText(expr)

    def _row_masking(self, row):
        """(masking, cast) de la fila: desarma la opcion combinada del combo."""
        key = self.cellWidget(row, COL_MASK).currentData()
        if key == MASK_INT_AS_STRING:
            return masking.MASK_INT, masking.CAST_BIGINT
        return key, None

    def update_salts(self, text_salt, int_salt):
        self._text_salt = text_salt or "saltTexto"
        self._int_salt = int_salt if int_salt not in (None, "") else "12345"
        for row in range(self.rowCount()):
            self._refresh_row(row)

    # -- filtro por nombre --------------------------------------------------
    def set_filter(self, text):
        """Oculta las filas cuyo nombre no se parece a `text`.

        Es solo vista: las filas ocultas conservan su marca, asi que filtrar no
        cambia lo que devuelven `selected_fields` / `selected_columns`.
        """
        self._filter = text or ""
        self._apply_filter()

    def _apply_filter(self):
        visible = 0
        for row in range(self.rowCount()):
            ok = matches(self._filter, self.item(row, COL_NAME).text())
            self.setRowHidden(row, not ok)
            visible += ok
        self.filterChanged.emit(visible, self.rowCount())

    def _visible_rows(self):
        return (row for row in range(self.rowCount()) if not self.isRowHidden(row))

    # -- seleccion ----------------------------------------------------------
    def set_all_checked(self, checked: bool):
        """Marca/desmarca las filas a la vista: con filtro activo, solo esas."""
        for row in self._visible_rows():
            chk = self.cellWidget(row, COL_USE).findChild(QCheckBox)
            chk.setChecked(checked)

    def _checked_rows(self):
        for row in range(self.rowCount()):
            chk = self.cellWidget(row, COL_USE).findChild(QCheckBox)
            if chk.isChecked():
                yield row

    def selected_fields(self):
        """Devuelve [{col, type, masking, cast?}] de las filas marcadas.

        `cast` solo aparece cuando se eligio el casteo: la clave ausente deja el
        JSON igual al de siempre y el SQL identico al de antes de esta opcion.
        """
        fields = []
        for row in self._checked_rows():
            mask, cast = self._row_masking(row)
            f = {
                "col": self.item(row, COL_NAME).text(),
                "type": self.item(row, COL_TYPE).text(),
                "masking": mask,
            }
            if cast:
                f["cast"] = cast
            fields.append(f)
        return fields

    def selected_columns(self):
        """Devuelve [{col, type}] de las filas marcadas, sin enmascaramiento."""
        return [
            {
                "col": self.item(row, COL_NAME).text(),
                "type": self.item(row, COL_TYPE).text(),
            }
            for row in self._checked_rows()
        ]


class ColumnFilterBar(QWidget):
    """Caja de busqueda + contador para una `ColumnTable`.

    Filtra mientras se escribe. El contador ('12 de 340') existe porque las
    tablas de origen traen cientos de columnas y, con el filtro puesto,
    'Seleccionar todo' solo toca las visibles: hay que ver cuantas son.
    """

    def __init__(self, table, parent=None):
        super().__init__(parent)
        self._table = table
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Buscar columna...")
        self.edit.setClearButtonEnabled(True)
        self.edit.textChanged.connect(table.set_filter)
        lay.addWidget(QLabel("Buscar:"))
        lay.addWidget(self.edit)

        self.count_label = QLabel("")
        table.filterChanged.connect(self._on_filter_changed)
        lay.addWidget(self.count_label)

    def _on_filter_changed(self, visible, total):
        self.count_label.setText(
            f"{visible} de {total}" if visible != total else f"{total} columnas"
        )
