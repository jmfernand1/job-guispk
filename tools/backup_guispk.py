"""Respaldo desatendido de las tablas guispk_* — para agendar.

La pestana Respaldo de la app interna protege contra el borrado, no contra el
olvido: lo que no se respaldo desde el ultimo clic, no esta. Este entry point
hace lo mismo sin UI, para dejarlo en el Programador de tareas / cron.

    python -m tools.backup_guispk                        # respalda
    python -m tools.backup_guispk --db D:\\ruta\\guispk.db  # ruta explicita
    python -m tools.backup_guispk --restore              # repuebla Impala
    python -m tools.backup_guispk --summary              # que hay en el .db

Credenciales: env vars USERNAME / PSWD / DSNLZ (las mismas de la app). No se
piden por consola — una tarea agendada no tiene quien las escriba — y no se
aceptan por argumento: quedarian en el historial de la shell y en la linea de
comandos de la tarea.

Ruta del .db: `--db`, o GUISPK_BACKUP_DB, o `backup_db` del config.ini.

Salida: una linea por tabla y un resumen. Codigo de salida 0 si todo fue bien,
1 si fallo — lo que necesita el agendador para avisar.
"""

import argparse
import os
import sys

from core.config import resolve_settings
from core.sparky_client import SparkyClient, credentials_from_env
from core.sparky_runner import SparkyRunner
from core.store import backup as backup_mod
from core.store import ddl


def _connect(settings):
    """Conexion Sparky con las credenciales del entorno. Solo equipo interno."""
    creds = credentials_from_env()
    faltan = [k for k in ("username", "password") if not creds[k]]
    if faltan:
        raise SystemExit(
            "Faltan credenciales en el entorno: "
            + ", ".join({"username": "USERNAME", "password": "PSWD"}[k] for k in faltan)
        )
    dsn = settings["dsn"] or creds["dsn"]
    if not dsn:
        raise SystemExit("Falta el DSN (GUISPK_DSN, config.ini o DSNLZ).")
    client = SparkyClient()
    client.connect(creds["username"], creds["password"], dsn)
    return SparkyRunner(client)


def _resolve_db(arg_db, settings) -> str:
    path = arg_db or settings["backup_db"]
    if not path:
        raise SystemExit(
            "Falta la ruta del respaldo: usa --db, la env var GUISPK_BACKUP_DB "
            "o la clave backup_db del config.ini."
        )
    return os.path.expanduser(path)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Respalda (o restaura) las tablas guispk_* de Impala."
    )
    parser.add_argument("--db", help="Ruta del .db de respaldo.")
    parser.add_argument("--schema", help="Esquema de las guispk_* (default: config).")
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument(
        "--restore", action="store_true",
        help="Reinserta en Impala las filas del respaldo que falten (no borra).",
    )
    modo.add_argument(
        "--summary", action="store_true",
        help="Solo informa que hay en el .db; no se conecta a Impala.",
    )
    args = parser.parse_args(argv)

    settings = resolve_settings()
    schema = args.schema or settings["schema"]
    db_path = _resolve_db(args.db, settings)

    try:
        if args.summary:
            counts = backup_mod.summary(db_path)
            for tabla, filas in counts.items():
                print(f"  {tabla}: {filas} filas")
            print(f"Respaldo {db_path}: {sum(counts.values())} filas en total.")
            return 0

        runner = _connect(settings)
        if args.restore:
            # El restore necesita las tablas creadas: si las borraron, esto las
            # recrea vacias y despues se repueblan desde el respaldo.
            ddl.ensure_remote_schema(runner, schema)
            counts = backup_mod.restore(runner, db_path, schema, log=print)
            total = sum(c["insertadas"] for c in counts.values())
            print(f"Restore desde {db_path}: {total} filas insertadas en {schema}.")
        else:
            counts = backup_mod.backup(runner, db_path, schema, log=print)
            print(f"Respaldo en {db_path}: {sum(counts.values())} filas.")
    except Exception as exc:  # noqa: BLE001 - el agendador solo ve el codigo
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
