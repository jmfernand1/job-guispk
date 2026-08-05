"""Tests de la capa de persistencia contra una BD en tmp_path."""

import pytest

from core import models, states
from core.store.catalog_repo import CatalogRepo
from core.store.db import ensure_db, open_db
from core.store.migrations import MIGRATIONS
from core.store.requests_repo import RequestsRepo

# lo que pide el aliado: columnas sin enmascaramiento
FIELDS = [
    {"col": "nombre", "type": "string"},
    {"col": "edad", "type": "int"},
]
# la decision del interno sobre esas columnas
FIELDS_FINAL = [
    {"col": "nombre", "type": "string", "masking": "mask_text"},
    {"col": "edad", "type": "int", "masking": "none"},
]


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "guispk.db")
    ensure_db(path)
    return path


def test_migrations_idempotent(db_path):
    ensure_db(db_path)  # segunda llamada no debe fallar ni duplicar
    with open_db(db_path) as con:
        version = con.execute("PRAGMA user_version").fetchone()[0]
    assert version == len(MIGRATIONS)


def test_v2_backfills_fields_final(tmp_path):
    """Una BD en V1 migra a V2 conservando la mascara vieja como decision final."""
    path = str(tmp_path / "vieja.db")
    with open_db(path) as con:
        con.executescript(MIGRATIONS[0])
        con.execute("PRAGMA user_version = 1")
        con.execute(
            "INSERT INTO requests (code, state, requester, created_at) "
            "VALUES ('REQ-1', 'enviada', 'aliado1', '2026-01-01T00:00:00Z')"
        )
        con.execute(
            "INSERT INTO request_items (request_id, src_table, dest_table, "
            "fields_json, sql_preview) VALUES (1, 'lz.t', 'p.t_enm', ?, '-- sql')",
            (models.fields_to_json(FIELDS_FINAL),),
        )
    ensure_db(path)
    item = RequestsRepo(path).get_request(1)["items"][0]
    assert item["fields_final"] == FIELDS_FINAL


def test_inventory_and_capture_roundtrip(db_path):
    repo = CatalogRepo(db_path)
    inv_id = repo.add_table("lz.clientes", "tabla de clientes", "interno1")
    repo.save_schema_capture(
        inv_id,
        "interno1",
        [("nombre", "string"), ("edad", "int")],
        partition_cols=["ingestion_day"],
        last_partition_where="ingestion_day = 2026-08-01",
        last_ingest="2026-08-01",
    )
    latest = repo.latest_schemas()
    assert len(latest) == 1
    entry = latest[0]
    assert entry["table_name"] == "lz.clientes"
    assert entry["capture_id"] is not None
    assert "nombre" in entry["columns_json"]

    # una captura nueva pasa a ser la vigente
    cap2 = repo.save_schema_capture(inv_id, "interno2", [("solo_una", "string")])
    assert repo.latest_schemas()[0]["capture_id"] == cap2

    # desactivar la tabla la saca del catalogo del aliado
    repo.set_active(inv_id, False)
    assert repo.latest_schemas() == []


def test_uncaptured_table_listed_pending(db_path):
    repo = CatalogRepo(db_path)
    repo.add_table("lz.sin_captura", "", "interno1")
    entry = repo.latest_schemas()[0]
    assert entry["capture_id"] is None


def _new_request_with_item(db_path, requester="aliado1"):
    repo = RequestsRepo(db_path)
    req = repo.create_request(requester)
    repo.add_item(
        req["id"], "lz.clientes", "proceso_enmascarado.clientes_enm",
        None, FIELDS, "ingestion_day = 2026-08-01", "-- sql preview",
    )
    return repo, req


def test_request_lifecycle(db_path):
    repo, req = _new_request_with_item(db_path)
    assert req["code"].startswith("REQ-")

    repo.transition(req["id"], states.ENVIADA, "aliado1", states.ROLE_ALIADO)
    # el interno ejecuta directo desde 'enviada', sin paso de aprobacion
    repo.transition(
        req["id"], states.EJECUTADA, "interno1", states.ROLE_INTERNO,
        execution_log="todo bien", executed_partition_where="ingestion_day = x",
    )

    final = repo.get_request(req["id"])
    assert final["state"] == states.EJECUTADA
    assert final["reviewed_by"] == "interno1"
    assert final["reviewed_at"]
    assert final["executed_by"] == "interno1"
    assert final["execution_log"] == "todo bien"
    assert final["items"][0]["fields"] == FIELDS
    assert len(final["items"]) == 1

    actions = [a["action"] for a in repo.audit_trail(req["id"])]
    assert actions == ["crear", "transicion", "transicion"]


def test_item_starts_without_final_fields(db_path):
    """Lo que crea el aliado no trae decision de enmascaramiento."""
    repo, req = _new_request_with_item(db_path)
    assert repo.get_request(req["id"])["items"][0]["fields_final"] is None


def test_set_item_final_fields_persists_and_audits(db_path):
    repo, req = _new_request_with_item(db_path)
    repo.transition(req["id"], states.ENVIADA, "aliado1", states.ROLE_ALIADO)
    item_id = repo.get_request(req["id"])["items"][0]["id"]

    repo.set_item_final_fields(item_id, FIELDS_FINAL, "interno1")

    item = repo.get_request(req["id"])["items"][0]
    assert item["fields_final"] == FIELDS_FINAL
    assert item["fields"] == FIELDS  # lo que pidio el aliado no se toca
    actions = [a["action"] for a in repo.audit_trail(req["id"])]
    assert "decision_enmascaramiento" in actions


def test_set_item_final_fields_requires_enviada(db_path):
    """En borrador (o ya ejecutada) no se puede pisar la decision."""
    repo, req = _new_request_with_item(db_path)
    item_id = repo.get_request(req["id"])["items"][0]["id"]
    with pytest.raises(states.TransitionError):
        repo.set_item_final_fields(item_id, FIELDS_FINAL, "interno1")


def test_invalid_transition_rejected(db_path):
    repo, req = _new_request_with_item(db_path)
    with pytest.raises(states.TransitionError):
        repo.transition(req["id"], states.EJECUTADA, "interno1", states.ROLE_INTERNO)
    # rol equivocado
    with pytest.raises(states.TransitionError):
        repo.transition(req["id"], states.ENVIADA, "interno1", states.ROLE_INTERNO)
    assert repo.get_request(req["id"])["state"] == states.BORRADOR


def test_reject_and_resubmit(db_path):
    repo, req = _new_request_with_item(db_path)
    repo.transition(req["id"], states.ENVIADA, "aliado1", states.ROLE_ALIADO)
    repo.transition(
        req["id"], states.RECHAZADA, "interno1", states.ROLE_INTERNO,
        comment="falta una columna",
    )
    assert repo.get_request(req["id"])["review_comment"] == "falta una columna"
    repo.transition(req["id"], states.BORRADOR, "aliado1", states.ROLE_ALIADO)
    repo.transition(req["id"], states.ENVIADA, "aliado1", states.ROLE_ALIADO)
    assert repo.get_request(req["id"])["state"] == states.ENVIADA


def test_concurrent_transition_fails_clean(db_path):
    """Dos procesos moviendo la misma solicitud: el segundo falla limpio."""
    repo_a, req = _new_request_with_item(db_path)
    repo_b = RequestsRepo(db_path)  # simula el otro proceso

    repo_a.transition(req["id"], states.ENVIADA, "aliado1", states.ROLE_ALIADO)
    # el "otro proceso" retira la solicitud primero
    repo_b.transition(req["id"], states.BORRADOR, "aliado1", states.ROLE_ALIADO)
    # este proceso cree que sigue enviada e intenta ejecutar
    with pytest.raises(states.TransitionError):
        repo_a.transition(req["id"], states.EJECUTADA, "interno1", states.ROLE_INTERNO)


def test_request_codes_are_sequential(db_path):
    repo = RequestsRepo(db_path)
    first = repo.create_request("aliado1")
    second = repo.create_request("aliado2")
    assert first["code"] != second["code"]
    assert first["code"].rsplit("-", 1)[0] == second["code"].rsplit("-", 1)[0]
