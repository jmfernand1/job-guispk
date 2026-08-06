"""Ninguna columna de las guispk_* puede llamarse como una palabra reservada.

Impala 3.0+ aplica la lista de reservadas ANSI SQL:2016: un CREATE TABLE con
una columna `at` o `role` falla al desplegar (paso en produccion). El test
recorre el DDL real, no una lista escrita a mano, para que una columna nueva
tambien quede cubierta.
"""

import re

from core.store import ddl

_COLUMN = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s+(STRING|BIGINT|INT|BOOLEAN)\s*,?\s*$")


def _columns_in_ddl():
    """Todas las columnas declaradas en los CREATE TABLE del esquema."""
    for stmt in ddl._ddl(ddl.DEFAULT_SCHEMA):
        for line in stmt.splitlines():
            match = _COLUMN.match(line)
            if match:
                yield match.group(1)


def test_ddl_declares_columns():
    """Guarda del propio parser: si deja de extraer columnas, el test miente."""
    assert len(list(_columns_in_ddl())) > 30


def test_no_reserved_column_names():
    assert ddl.reserved_columns(_columns_in_ddl()) == []


def test_reserved_columns_detects_known_offenders():
    """Los nombres que rompieron el despliegue siguen marcados como reservados."""
    offenders = ["at", "who", "role", "comment"]
    assert ddl.reserved_columns(offenders) == ["at", "role", "comment"]
