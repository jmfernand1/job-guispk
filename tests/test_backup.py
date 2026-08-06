"""Respaldo a SQLite y restauracion tras un borrado de las tablas guispk_*."""

import pytest

from core import models, states
from core.store import backup, ddl
from core.store.catalog_repo import CatalogRepo
from core.store.history_repo import HistoryRepo
from core.store.requests_repo import RequestsRepo
from tests.fake_impala import new_runner_with_schema

_SCRIPT_CON_PLACEHOLDERS = (
    "CREATE TABLE dst AS SELECT sha2(concat(doc, '{{TEXT_SALT}}'), 256) FROM src"
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "onedrive" / "guispk_backup.db")


def _poblar(runner):
    """Deja algo de cada una de las cinco tablas."""
    catalog = CatalogRepo(runner)
    catalog.add_table("zona.clientes", "Maestro de clientes", "interno1")
    capture_id = catalog.save_schema_capture(
        "zona.clientes", "interno1", [("doc", "string"), ("nombre", "string")]
    )

    requests = RequestsRepo(runner)
    req = requests.create_request("aliado1")
    requests.add_item(
        req["id"], "zona.clientes", "proceso_enmascarado.clientes_masked",
        capture_id, [{"col": "doc", "masking": "sha256"}], None, "SELECT 1",
    )
    requests.transition(req["id"], states.ENVIADA, "aliado1", states.ROLE_ALIADO)
    requests.transition(
        req["id"], states.EJECUTADA, "interno1", states.ROLE_INTERNO,
        comment="ok", execution_log="listo",
    )

    HistoryRepo(runner).record(
        "interno1", "solicitud", "zona.clientes",
        "proceso_enmascarado.clientes_masked", _SCRIPT_CON_PLACEHOLDERS,
        request_code=req["code"],
    )
    return req


def test_backup_copia_las_cinco_tablas(runner_poblado, db_path):
    counts = backup.backup(runner_poblado, db_path)
    assert set(counts) == set(ddl.SCHEMA)
    assert all(n > 0 for n in counts.values())
    assert backup.summary(db_path) == counts


def test_restore_tras_borrado_total(runner_poblado, db_path):
    """El desastre: borran las guispk_*, se recrean y se restaura el respaldo."""
    esperado = RequestsRepo(runner_poblado).list_requests()
    backup.backup(runner_poblado, db_path)

    vacio = new_runner_with_schema()  # tablas recreadas, sin datos
    counts = backup.restore(vacio, db_path)

    assert all(c["omitidas"] == 0 for c in counts.values())
    restaurado = RequestsRepo(vacio).list_requests()
    assert restaurado == esperado
    assert restaurado[0]["state"] == states.EJECUTADA
    assert CatalogRepo(vacio).latest_schemas()[0]["table_name"] == "zona.clientes"
    assert len(HistoryRepo(vacio).list_scripts()) == 1


def test_restore_dos_veces_no_duplica(runner_poblado, db_path):
    """Correrlo de nuevo es inocuo: un evento repetido corromperia el fold."""
    backup.backup(runner_poblado, db_path)
    vacio = new_runner_with_schema()

    backup.restore(vacio, db_path)
    eventos = len(vacio.query(f"SELECT * FROM {ddl.qname('request_events')}"))
    segunda = backup.restore(vacio, db_path)

    assert all(c["insertadas"] == 0 for c in segunda.values())
    assert len(vacio.query(f"SELECT * FROM {ddl.qname('request_events')}")) == eventos
    assert len(RequestsRepo(vacio).list_requests()) == 1


def test_restore_completa_un_restore_a_medias(runner_poblado, db_path):
    """Si el restore se corta, el siguiente inserta solo lo que falta."""
    backup.backup(runner_poblado, db_path)
    parcial = new_runner_with_schema()

    datos = backup.read_backup(db_path)
    evento = datos["request_events"][0]
    cols = ddl.SCHEMA["request_events"]["columns"]
    parcial.execute(
        f"INSERT INTO {ddl.qname('request_events')} ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)})",
        tuple(evento[c] for c in cols),
    )

    counts = backup.restore(parcial, db_path)
    assert counts["request_events"]["omitidas"] == 1
    assert len(RequestsRepo(parcial).list_requests()) == 1


def test_el_respaldo_no_contiene_salts(runner_poblado, db_path):
    """Fallo de seguridad si falla: el .db va a OneDrive, no a la boveda.

    El respaldo copia lo que hay en las guispk_*, y ahi los scripts llevan
    placeholders. Se verifica sobre el archivo crudo: si algun dia alguien
    resolviera los salts antes de guardar, el placeholder desapareceria y el
    salt real quedaria en un .db sincronizado.
    """
    HistoryRepo(runner_poblado).record(
        "interno1", "adhoc", "zona.clientes", "proceso_enmascarado.otra",
        _SCRIPT_CON_PLACEHOLDERS, salt_label="proyecto-x-2026",
    )
    backup.backup(runner_poblado, db_path)

    guardados = backup.read_backup(db_path)["script_history"]
    # Copia literal: el script sale del respaldo igual que entro. Si alguien
    # hiciera que el respaldo resolviera los salts, esto falla.
    assert all(h["script"] == _SCRIPT_CON_PLACEHOLDERS for h in guardados)
    assert all("{{TEXT_SALT}}" in h["script"] for h in guardados)
    # La etiqueta si viaja (identifica el par de salts, no lo revela).
    assert "proyecto-x-2026" in {h["salt_label"] for h in guardados}


def test_restore_sin_archivo_falla_claro(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup.restore(new_runner_with_schema(), str(tmp_path / "no-existe.db"))


def test_backup_reemplaza_y_no_acumula(runner_poblado, db_path):
    """Dos respaldos seguidos: foto del estado, no historico incremental."""
    primero = backup.backup(runner_poblado, db_path)
    segundo = backup.backup(runner_poblado, db_path)
    assert primero == segundo


@pytest.fixture
def runner_poblado():
    runner = new_runner_with_schema()
    _poblar(runner)
    return runner
