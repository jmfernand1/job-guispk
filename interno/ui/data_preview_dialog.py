"""Muestra una muestra de datos del origen, sin enmascarar.

Sirve para una decision concreta: distinguir las columnas que el DESCRIBE
declara string pero que en realidad guardan enteros (esas se enmascaran con
mask_int y casteo). Es solo lectura y no se guarda nada: los datos que aparecen
aqui son los del origen tal cual, asi que la ventana se cierra y no deja rastro.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class DataPreviewDialog(QDialog):
    """Ventana no modal con las filas de muestra y el SELECT que las trajo."""

    def __init__(self, src_table, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Vista previa - {src_table}")
        self.resize(900, 500)

        lay = QVBoxLayout(self)
        self.status_label = QLabel(f"Consultando {src_table}...")
        lay.addWidget(self.status_label)

        self.sql_view = QPlainTextEdit()
        self.sql_view.setReadOnly(True)
        self.sql_view.setMaximumHeight(90)
        lay.addWidget(self.sql_view)

        self.table = QTableWidget(0, 0)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        lay.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)

    def show_dataframe(self, df, sql=None):
        """Vuelca el DataFrame en la tabla. Los valores se muestran como texto."""
        if sql:
            self.sql_view.setPlainText(sql)
        columnas = [str(c) for c in df.columns]
        self.table.setColumnCount(len(columnas))
        self.table.setHorizontalHeaderLabels(columnas)
        self.table.setRowCount(len(df.index))
        for row, (_, fila) in enumerate(df.iterrows()):
            for col, name in enumerate(df.columns):
                valor = fila[name]
                item = QTableWidgetItem("" if valor is None else str(valor))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.status_label.setText(
            f"{len(df.index)} filas · {len(columnas)} columnas. "
            "Revisa si alguna columna de texto guarda solo enteros."
        )

    def show_error(self, msg):
        self.status_label.setText(f"Error: {msg}")
