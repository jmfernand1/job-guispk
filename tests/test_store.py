"""Tests de la capa de persistencia contra el runner falso de Impala."""

import pytest

from core import states
from core.store import ddl
from core.store.catalog_repo import CatalogRepo
from core.store.requests_repo import RequestsRepo
from tests.fake_impala import new_runner_with_schema

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
def runner():
    return new_runner_with_schema()


def test_ensure_remote_schema_idempotent(runner):
    ddl.ensure_remote_schema(runner)  # segunda llamada no debe fallar
    # y las tablas responden vacias
    assert runner.query(f"SELECT * FROM {ddl.qname('request_events')}") == []


def test_inventory_and_capture_roundtrip(runner):
    repo = CatalogRepo(runner)
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
    repo.set_active(inv_id, False, "interno1")
    assert repo.latest_schemas() == []

    # y reactivarla la devuelve (el fold aplica el ultimo evento)
    repo.set_active(inv_id, True, "interno1")
    assert repo.latest_schemas()[0]["table_name"] == "lz.clientes"


def test_uncaptured_table_listed_pending(runner):
    repo = CatalogRepo(runner)
    repo.add_table("lz.sin_captura", "", "interno1")
    entry = repo.latest_schemas()[0]
    assert entry["capture_id"] is None


def test_get_capture_includes_table_name(runner):
    repo = CatalogRepo(runner)
    inv_id = repo.add_table("lz.clientes", "", "interno1")
    cap_id = repo.save_schema_capture(inv_id, "interno1", [("a", "string")])
    cap = repo.get_capture(cap_id)
    assert cap["table_name"] == "lz.clientes"
    assert repo.get_capture("no-existe") is None


def _new_request_with_item(runner, requester="aliado1"):
    repo = RequestsRepo(runner)
    req = repo.create_request(requester)
    repo.add_item(
        req["id"], "lz.clientes", "proceso_enmascarado.clientes_enm",
        None, FIELDS, "ingestion_day = 2026-08-01", "-- sql preview",
    )
    return repo, req


def test_request_lifecycle(runner):
    repo, req = _new_request_with_item(runner)
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


def test_item_starts_without_final_fields(runner):
    """Lo que crea el aliado no trae decision de enmascaramiento."""
    repo, req = _new_request_with_item(runner)
    assert repo.get_request(req["id"])["items"][0]["fields_final"] is None


def test_set_item_final_fields_persists_and_audits(runner):
    repo, req = _new_request_with_item(runner)
    repo.transition(req["id"], states.ENVIADA, "aliado1", states.ROLE_ALIADO)
    item_id = repo.get_request(req["id"])["items"][0]["id"]

    repo.set_item_final_fields(item_id, FIELDS_FINAL, "interno1")

    item = repo.get_request(req["id"])["items"][0]
    assert item["fields_final"] == FIELDS_FINAL
    assert item["fields"] == FIELDS  # lo que pidio el aliado no se toca
    actions = [a["action"] for a in repo.audit_trail(req["id"])]
    assert "decision_enmascaramiento" in actions


def test_final_fields_survive_execution(runner):
    """La decision tomada en enviada sigue visible tras ejecutar."""
    repo, req = _new_request_with_item(runner)
    repo.transition(req["id"], states.ENVIADA, "aliado1", states.ROLE_ALIADO)
    item_id = repo.get_request(req["id"])["items"][0]["id"]
    repo.set_item_final_fields(item_id, FIELDS_FINAL, "interno1")
    repo.transition(req["id"], states.EJECUTADA, "interno1", states.ROLE_INTERNO)
    assert repo.get_request(req["id"])["items"][0]["fields_final"] == FIELDS_FINAL


def test_set_item_final_fields_requires_enviada(runner):
    """En borrador (o ya ejecutada) no se puede pisar la decision."""
    repo, req = _new_request_with_item(runner)
    item_id = repo.get_request(req["id"])["items"][0]["id"]
    with pytest.raises(states.TransitionError):
        repo.set_item_final_fields(item_id, FIELDS_FINAL, "interno1")


def test_invalid_transition_rejected(runner):
    repo, req = _new_request_with_item(runner)
    with pytest.raises(states.TransitionError):
        repo.transition(req["id"], states.EJECUTADA, "interno1", states.ROLE_INTERNO)
    # rol equivocado
    with pytest.raises(states.TransitionError):
        repo.transition(req["id"], states.ENVIADA, "interno1", states.ROLE_INTERNO)
    assert repo.get_request(req["id"])["state"] == states.BORRADOR


def test_reject_and_resubmit(runner):
    repo, req = _new_request_with_item(runner)
    repo.transition(req["id"], states.ENVIADA, "aliado1", states.ROLE_ALIADO)
    repo.transition(
        req["id"], states.RECHAZADA, "interno1", states.ROLE_INTERNO,
        comment="falta una columna",
    )
    assert repo.get_request(req["id"])["review_comment"] == "falta una columna"
    repo.transition(req["id"], states.BORRADOR, "aliado1", states.ROLE_ALIADO)
    repo.transition(req["id"], states.ENVIADA, "aliado1", states.ROLE_ALIADO)
    assert repo.get_request(req["id"])["state"] == states.ENVIADA


def test_concurrent_transition_fails_clean(runner):
    """Dos procesos moviendo la misma solicitud: el segundo falla limpio."""
    repo_a, req = _new_request_with_item(runner)
    repo_b = RequestsRepo(runner)  # simula el otro proceso

    repo_a.transition(req["id"], states.ENVIADA, "aliado1", states.ROLE_ALIADO)
    # el "otro proceso" retira la solicitud primero
    repo_b.transition(req["id"], states.BORRADOR, "aliado1", states.ROLE_ALIADO)
    # este proceso cree que sigue enviada e intenta ejecutar
    with pytest.raises(states.TransitionError):
        repo_a.transition(req["id"], states.EJECUTADA, "interno1", states.ROLE_INTERNO)


def test_race_lost_event_is_ignored_by_fold(runner):
    """Un evento que perdio la carrera queda inerte: el fold no lo aplica.

    Simula la carrera insertando a mano un evento 'transicion' con from_state
    viejo (como si otro proceso hubiera derivado el estado antes del INSERT
    ganador): el estado no cambia y la auditoria no lo registra.
    """
    repo, req = _new_request_with_item(runner)
    repo.transition(req["id"], states.ENVIADA, "aliado1", states.ROLE_ALIADO)
    repo.transition(req["id"], states.BORRADOR, "aliado1", states.ROLE_ALIADO)

    # evento perdedor: cree que la solicitud sigue enviada
    repo._insert_event(
        request_id=req["id"], code=req["code"], event_by="interno1",
        actor_role=states.ROLE_INTERNO, event_type="transicion",
        from_state=states.ENVIADA, to_state=states.EJECUTADA,
    )
    final = repo.get_request(req["id"])
    assert final["state"] == states.BORRADOR
    assert final["executed_by"] is None
    actions = [a["action"] for a in repo.audit_trail(req["id"])]
    assert actions == ["crear", "transicion", "transicion"]


def test_list_requests_filters(runner):
    repo, req1 = _new_request_with_item(runner, requester="aliado1")
    _, req2 = _new_request_with_item(runner, requester="aliado2")
    repo.transition(req2["id"], states.ENVIADA, "aliado2", states.ROLE_ALIADO)

    assert {r["code"] for r in repo.list_requests()} == {req1["code"], req2["code"]}
    enviadas = repo.list_requests(state=states.ENVIADA)
    assert [r["code"] for r in enviadas] == [req2["code"]]
    de_aliado1 = repo.list_requests(requester="aliado1")
    assert [r["code"] for r in de_aliado1] == [req1["code"]]


def test_request_codes_are_unique(runner):
    repo = RequestsRepo(runner)
    first = repo.create_request("aliado1")
    second = repo.create_request("aliado2")
    assert first["code"] != second["code"]
    assert first["code"].rsplit("-", 1)[0] == second["code"].rsplit("-", 1)[0]
