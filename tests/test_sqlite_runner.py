"""El backend SQLite: mismos repos, mismo archivo que el respaldo.

Lo que importa aqui es que el archivo sea intercambiable con el del respaldo
(para poder trabajar sobre el espejo durante una caida del DSN y despues subir
lo escrito con el `restore` de siempre) y que sobreviva a cerrar la app.
"""

import threading

import pytest

from core import config, states
from core.store import backup as backup_mod
from core.store import ddl
from core.store.catalog_repo import CatalogRepo
from core.store.requests_repo import RequestsRepo
from core.store.sqlite_runner import SqliteRunner
from tests.fake_impala import new_runner_with_schema

FIELDS = [{"col": "nombre", "type": "string"}, {"col": "edad", "type": "int"}]


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "espejo" / "guispk.db")


@pytest.fixture
def runner(db_path):
    r = SqliteRunner(db_path)
    yield r
    r.close()


def _crear_solicitud(runner):
    """Crea una solicitud con un item. Devuelve (repo, id, code)."""
    repo = RequestsRepo(runner)
    req = repo.create_request("aliado1")
    repo.add_item(
        req["id"],
        src_table="lz.clientes",
        dest_table="proceso_enmascarado.clientes_enm",
        schema_capture_id=None,
        fields=FIELDS,
        partition_where_requested=None,
        sql_preview="-- Solicitud",
    )
    return repo, req["id"], req["code"]


# -- el runner cumple lo que los repos esperan -----------------------------
def test_los_repos_funcionan_igual_que_contra_impala(runner):
    repo, req_id, _ = _crear_solicitud(runner)
    repo.transition(req_id, states.ENVIADA, "aliado1", states.ROLE_ALIADO)

    req = repo.get_request(req_id)
    assert req["state"] == states.ENVIADA
    assert req["items"][0]["fields"] == FIELDS


def test_el_catalogo_tambien(runner):
    repo = CatalogRepo(runner)
    inv_id = repo.add_table("lz.clientes", "clientes", "interno1")
    repo.save_schema_capture(
        inv_id, "interno1", [("nombre", "string")], partition_cols=["anio"]
    )
    assert [s["table_name"] for s in repo.latest_schemas()] == ["lz.clientes"]


def test_la_decision_con_casteo_viaja_entera(runner):
    """El campo nuevo de F2 se guarda y se lee igual que los demas."""
    repo, req_id, _ = _crear_solicitud(runner)
    repo.transition(req_id, states.ENVIADA, "aliado1", states.ROLE_ALIADO)
    final = [{
        "col": "nombre",
        "type": "string",
        "masking": "mask_int",
        "cast": "bigint",
    }]
    item_id = repo.get_request(req_id)["items"][0]["item_id"]
    repo.set_item_final_fields(item_id, final, "interno1")

    assert repo.get_request(req_id)["items"][0]["fields_final"] == final


# -- es el mismo archivo del respaldo --------------------------------------
def test_abre_un_respaldo_existente_tal_cual(db_path):
    """Un .db escrito por backup() se lee con los repos sin convertir nada."""
    impala = new_runner_with_schema()
    _, _, code = _crear_solicitud(impala)
    backup_mod.backup(impala, db_path)

    runner = SqliteRunner(db_path)
    try:
        codigos = [r["code"] for r in RequestsRepo(runner).list_requests()]
    finally:
        runner.close()
    assert codigos == [code]


def test_lo_escrito_en_sqlite_sube_a_impala_con_restore(db_path, runner):
    """El camino de vuelta cuando el DSN se recupera: restore sin duplicar."""
    _, _, code = _crear_solicitud(runner)
    runner.close()

    impala = new_runner_with_schema()
    backup_mod.restore(impala, db_path)
    assert [r["code"] for r in RequestsRepo(impala).list_requests()] == [code]

    # segunda pasada: no duplica (append-only)
    counts = backup_mod.restore(impala, db_path)
    assert counts["request_events"]["insertadas"] == 0


def test_persiste_al_reabrir(db_path):
    r1 = SqliteRunner(db_path)
    _, _, code = _crear_solicitud(r1)
    r1.close()

    r2 = SqliteRunner(db_path)
    try:
        assert [r["code"] for r in RequestsRepo(r2).list_requests()] == [code]
    finally:
        r2.close()


def test_los_ids_se_guardan_como_texto(runner):
    """STRING->TEXT: un id de solo digitos no puede volverse float."""
    runner.execute(
        f"INSERT INTO {ddl.qname('script_history')} (script_id) VALUES (?)",
        ("12345678901234567890",),
    )
    filas = runner.query(f"SELECT script_id FROM {ddl.qname('script_history')}")
    assert filas[0]["script_id"] == "12345678901234567890"


def test_varios_hilos_no_se_pisan(runner):
    def escribir(i):
        RequestsRepo(runner).create_request(f"aliado{i}")

    hilos = [threading.Thread(target=escribir, args=(i,)) for i in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert len(RequestsRepo(runner).list_requests()) == 8


def test_sin_ruta_no_se_construye():
    with pytest.raises(ValueError):
        SqliteRunner("")


# -- configuracion ---------------------------------------------------------
def test_backend_por_defecto_es_impala(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for var in ("GUISPK_BACKEND", "GUISPK_SQLITE_PATH", "GUISPK_BACKUP_DB"):
        monkeypatch.delenv(var, raising=False)
    assert config.resolve_settings()["backend"] == config.BACKEND_IMPALA


def test_backend_sqlite_por_entorno(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GUISPK_BACKEND", "SQLite")
    monkeypatch.setenv("GUISPK_SQLITE_PATH", "~/espejo/guispk.db")
    settings = config.resolve_settings()
    assert settings["backend"] == config.BACKEND_SQLITE
    assert not settings["sqlite_path"].startswith("~")


def test_un_backend_desconocido_cae_a_impala(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GUISPK_BACKEND", "oracle")
    assert config.resolve_settings()["backend"] == config.BACKEND_IMPALA


def test_sin_ruta_propia_se_usa_la_del_respaldo(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GUISPK_SQLITE_PATH", raising=False)
    monkeypatch.setenv("GUISPK_BACKUP_DB", str(tmp_path / "respaldo.db"))
    settings = config.resolve_settings()
    assert settings["sqlite_path"] == settings["backup_db"]
