"""Respaldo de las tablas `guispk_*` a un SQLite, y restauracion desde el.

Las `guispk_*` viven en `proceso_enmascarado`, un esquema que el equipo interno
no administra: un borrado ajeno (limpieza de zona, DROP por error) se lleva
inventario, catalogo, solicitudes e historico. El respaldo es el seguro: un
archivo `.db` en la carpeta OneDrive del equipo interno — que ellos si pueden
sincronizar — con una copia fiel de las cinco tablas.

**Copia fiel, no formato propio.** El SQLite tiene las mismas tablas y las
mismas columnas que Impala (se generan de `ddl.SCHEMA`), asi que restaurar es
volver a insertar las filas tal cual. Sin conversiones que se puedan
desalinear con el tiempo.

**El restore respeta append-only**: no borra ni actualiza nada, solo inserta
las filas cuyo id no esta ya en Impala. Se puede correr sobre tablas vacias
(el caso del desastre), sobre tablas a medio restaurar, o dos veces seguidas
sin duplicar eventos — y un evento duplicado no seria inocuo: el fold de
`requests_repo` lo veria como una transicion mas.

**Sin secretos**, igual que las tablas de origen: los scripts van con
`{{TEXT_SALT}}` / `{{INT_SALT}}` porque asi estan guardados. El respaldo no
puede filtrar lo que la fuente no tiene.

Lo corre solo la app interna (pestana Respaldo); el aliado ni ve este modulo.
"""

import os
import sqlite3

from core.store import ddl

# Se lee y se escribe por lotes: son cinco tablas chicas, pero un INSERT por
# fila contra Impala son segundos por fila.
_INSERT_CHUNK = 200


def _sqlite_ddl(name: str) -> str:
    """CREATE TABLE espejo: mismas columnas, todas TEXT (STRING en Impala)."""
    cols = ", ".join(f"{c} TEXT" for c in ddl.SCHEMA[name]["columns"])
    return f"CREATE TABLE IF NOT EXISTS {ddl.PREFIX}{name} ({cols})"


def _connect(path: str) -> sqlite3.Connection:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # journal_mode=DELETE y synchronous=FULL: el archivo vive en OneDrive/SMB,
    # donde WAL no es confiable (misma razon que en core/store/db.py).
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA synchronous=FULL")
    return con


def backup(runner, db_path: str, schema: str = ddl.DEFAULT_SCHEMA, log=None) -> dict:
    """Vuelca las `guispk_*` de Impala al SQLite `db_path`.

    Cada tabla se reemplaza entera (DELETE + INSERT dentro de una transaccion):
    el respaldo es una foto del estado actual, no un incremental. Devuelve
    {tabla: filas}.
    """
    counts = {}
    con = _connect(db_path)
    try:
        for name, spec in ddl.SCHEMA.items():
            cols = spec["columns"]
            rows = runner.query(f"SELECT * FROM {ddl.qname(name, schema)}")
            con.execute(_sqlite_ddl(name))
            # Todo o nada por tabla: si la copia se corta a la mitad, el
            # respaldo anterior de esa tabla sigue en pie.
            with con:
                con.execute(f"DELETE FROM {ddl.PREFIX}{name}")
                con.executemany(
                    f"INSERT INTO {ddl.PREFIX}{name} ({', '.join(cols)}) "
                    f"VALUES ({', '.join('?' for _ in cols)})",
                    [tuple(r.get(c) for c in cols) for r in rows],
                )
            counts[name] = len(rows)
            if log:
                log(f"  {name}: {len(rows)} filas respaldadas")
    finally:
        con.close()
    return counts


def read_backup(db_path: str) -> dict:
    """{tabla: [filas]} del respaldo. Tabla ausente en el .db -> lista vacia."""
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"No existe el respaldo: {db_path}")
    data = {}
    con = _connect(db_path)
    try:
        existing = {
            r["name"]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for name, spec in ddl.SCHEMA.items():
            table = f"{ddl.PREFIX}{name}"
            if table not in existing:
                data[name] = []
                continue
            cols = ", ".join(spec["columns"])
            data[name] = [
                dict(r) for r in con.execute(f"SELECT {cols} FROM {table}")
            ]
    finally:
        con.close()
    return data


def _existing_ids(runner, name: str, schema: str) -> set:
    id_col = ddl.SCHEMA[name]["id"]
    rows = runner.query(f"SELECT {id_col} FROM {ddl.qname(name, schema)}")
    return {r[id_col] for r in rows}


def restore(runner, db_path: str, schema: str = ddl.DEFAULT_SCHEMA, log=None) -> dict:
    """Reinserta en Impala las filas del respaldo que no estan ya ahi.

    Requiere el esquema creado (`ddl.ensure_remote_schema`): tras un borrado
    de tablas hay que recrearlas antes. Devuelve
    {tabla: {"insertadas": n, "omitidas": n}} — `omitidas` son las que ya
    estaban, la senal de que el restore no duplico nada.
    """
    data = read_backup(db_path)
    counts = {}
    for name, spec in ddl.SCHEMA.items():
        cols = spec["columns"]
        id_col = spec["id"]
        present = _existing_ids(runner, name, schema)
        pending = [r for r in data[name] if r[id_col] not in present]
        table = ddl.qname(name, schema)
        placeholders = f"({', '.join('?' for _ in cols)})"
        for start in range(0, len(pending), _INSERT_CHUNK):
            chunk = pending[start:start + _INSERT_CHUNK]
            params = tuple(v for r in chunk for v in (r.get(c) for c in cols))
            runner.execute(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES "
                + ", ".join(placeholders for _ in chunk),
                params,
            )
        counts[name] = {
            "insertadas": len(pending),
            "omitidas": len(data[name]) - len(pending),
        }
        if log:
            log(
                f"  {name}: {len(pending)} insertadas, "
                f"{len(data[name]) - len(pending)} ya estaban"
            )
    return counts


def summary(db_path: str) -> dict:
    """{tabla: filas} de un respaldo existente, sin tocar Impala."""
    return {name: len(rows) for name, rows in read_backup(db_path).items()}
