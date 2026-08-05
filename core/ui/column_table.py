"""QTableWidget para seleccionar campos y su enmascaramiento."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
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


class ColumnTable(QTableWidget):
    """Una fila por columna: [Usar] | nombre | tipo | [combo] | preview.

    Con `with_masking=False` (modo aliado) se ocultan las columnas de
    enmascaramiento y vista previa: el aliado solo elige que columnas necesita
    y el enmascaramiento lo decide el interno.
    """

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

    # -- carga --------------------------------------------------------------
    def load_columns(self, columns):
        """columns: lista de (name, type). Llena la tabla y autoselecciona."""
        self.setRowCount(0)
        for name, ctype in columns:
            self._add_row(name, ctype)

    def load_fields(self, fields):
        """fields: [{col, type, masking?}]. Restaura una seleccion guardada.

        Si el campo trae `masking` se preselecciona en el combo; si no, queda
        la sugerencia automatica por tipo.
        """
        self.setRowCount(0)
        for f in fields:
            self._add_row(f["col"], f["type"], f.get("masking"))

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
        for key in _MASK_ORDER:
            combo.addItem(masking.LABELS[key], key)
        if mask not in _MASK_ORDER:
            mask = masking.suggest_masking(ctype)
        combo.setCurrentIndex(_MASK_ORDER.index(mask))
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
        combo = self.cellWidget(row, COL_MASK)
        mask = combo.currentData()
        expr = masking.select_expr(name, mask, self._text_salt, self._int_salt)
        self.item(row, COL_PREVIEW).setText(expr)

    def update_salts(self, text_salt, int_salt):
        self._text_salt = text_salt or "saltTexto"
        self._int_salt = int_salt if int_salt not in (None, "") else "12345"
        for row in range(self.rowCount()):
            self._refresh_row(row)

    # -- seleccion ----------------------------------------------------------
    def set_all_checked(self, checked: bool):
        for row in range(self.rowCount()):
            chk = self.cellWidget(row, COL_USE).findChild(QCheckBox)
            chk.setChecked(checked)

    def _checked_rows(self):
        for row in range(self.rowCount()):
            chk = self.cellWidget(row, COL_USE).findChild(QCheckBox)
            if chk.isChecked():
                yield row

    def selected_fields(self):
        """Devuelve [{col, type, masking}] de las filas marcadas."""
        return [
            {
                "col": self.item(row, COL_NAME).text(),
                "type": self.item(row, COL_TYPE).text(),
                "masking": self.cellWidget(row, COL_MASK).currentData(),
            }
            for row in self._checked_rows()
        ]

    def selected_columns(self):
        """Devuelve [{col, type}] de las filas marcadas, sin enmascaramiento."""
        return [
            {
                "col": self.item(row, COL_NAME).text(),
                "type": self.item(row, COL_TYPE).text(),
            }
            for row in self._checked_rows()
        ]
