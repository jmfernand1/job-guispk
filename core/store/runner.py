"""Protocolo de ejecucion SQL contra la BD de coordinacion (Impala).

Los repos generan SQL con placeholders `?` y una lista de parametros; cada
runner resuelve el binding a su manera:

- pyodbc (aliado) y sqlite3 (tests) soportan `?` nativo.
- Sparky (interno) no acepta parametros: su adapter sustituye cada `?` por el
  literal escapado en dialecto Impala (ver interno/sparky_runner.py).

Regla para los repos: nunca escribir el caracter `?` dentro de literales del
template — todo valor variable viaja como parametro.
"""

from typing import Protocol


class ImpalaRunner(Protocol):
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """SELECT: devuelve filas como lista de dicts columna->valor."""
        ...

    def execute(self, sql: str, params: tuple = ()) -> None:
        """DDL / INSERT: ejecuta sin devolver filas."""
        ...


def impala_literal(value) -> str:
    """Literal SQL en dialecto Impala (escape con backslash).

    Para runners que no soportan binding de parametros (Sparky) o donde no
    conviene depender de el (ODBC del aliado): el SQL viaja ya resuelto.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"


def inline_params(sql: str, params: tuple, escape=impala_literal) -> str:
    """Sustituye cada `?` por su literal escapado (para runners sin binding).

    `escape(value) -> str` produce el literal SQL. Se recorre en una sola
    pasada: un `?` dentro de un valor ya sustituido no se reinterpreta.
    """
    parts = sql.split("?")
    if len(parts) - 1 != len(params):
        raise ValueError(
            f"El SQL tiene {len(parts) - 1} placeholders y llegaron "
            f"{len(params)} parametros."
        )
    out = [parts[0]]
    for value, part in zip(params, parts[1:]):
        out.append(escape(value))
        out.append(part)
    return "".join(out)
