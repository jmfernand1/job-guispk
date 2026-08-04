"""Navegador del catalogo: tablas autorizadas con su ultima captura de esquema."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem

_COL_TABLE = 0
_COL_CAPTURED = 1
_COL_PARTITION = 2
_COL_DESC = 3


class CatalogBrowser(QTableWidget):
    """Lista de solo lectura; emite la entrada completa al seleccionar una fila.

    Cada entrada es un dict de CatalogRepo.latest_schemas(); las tablas sin
    captura (capture_id NULL) se muestran deshabilitadas.
    """

    entrySelected = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(0, 4, parent)
        self.setHorizontalHeaderLabels(
            ["Tabla", "Esquema capturado", "Particion (snapshot)", "Descripcion"]
        )
        self.horizontalHeader().setStretchLastSection(True)
        self.setColumnWidth(_COL_TABLE, 260)
        self.setColumnWidth(_COL_CAPTURED, 150)
        self.setColumnWidth(_COL_PARTITION, 200)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._entries = []
        self.itemSelectionChanged.connect(self._on_selection)

    def load(self, entries):
        """entries: lista de dicts de CatalogRepo.latest_schemas()."""
        self._entries = entries
        self.setRowCount(0)
        for e in entries:
            row = self.rowCount()
            self.insertRow(row)
            captured = e["captured_at"] or "SIN CAPTURAR"
            values = [
                e["table_name"],
                captured,
                e["last_partition_where"] or "-",
                e["description"] or "",
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                if e["capture_id"] is None:
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                self.setItem(row, col, item)

    def _on_selection(self):
        row = self.currentRow()
        if 0 <= row < len(self._entries) and self._entries[row]["capture_id"]:
            self.entrySelected.emit(self._entries[row])

    def selected_entry(self):
        row = self.currentRow()
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None
