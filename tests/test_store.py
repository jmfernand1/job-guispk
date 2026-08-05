"""Tests de la capa de persistencia contra una BD en tmp_path."""

import pytest

from core import states
from core.store.catalog_repo import CatalogRepo
from core.store.db import ensure_db, open_db
from core.store.requests_repo import RequestsRepo

FIELDS = [
    {"col": "nombre", "type": "string", "masking": "mask_text"},
    {"col": "edad", "type": "int", "masking": "mask_int"},
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
    assert version == 1


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
