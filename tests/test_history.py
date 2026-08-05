"""Tests del historico de scripts ejecutados."""

import pytest

from core import masking
from core.store.history_repo import (
    ORIGIN_ADHOC,
    ORIGIN_SOLICITUD,
    STATUS_ERROR,
    STATUS_OK,
    HistoryRepo,
)
from tests.fake_impala import new_runner_with_schema

SCRIPT = (
    "-- Script de enmascaramiento\n"
    "DROP TABLE IF EXISTS p.t_enm PURGE;\n"
    "CREATE TABLE IF NOT EXISTS p.t_enm (nombre STRING) STORED AS PARQUET;\n"
    "INSERT INTO p.t_enm SELECT default.mask_text(nombre, '{{TEXT_SALT}}') "
    "AS nombre FROM lz.t;"
)


@pytest.fixture
def repo():
    return HistoryRepo(new_runner_with_schema())


def _record(repo, **kw):
    base = dict(
        who="interno1",
        origin=ORIGIN_SOLICITUD,
        src_table="lz.t",
        dest_table="p.t_enm",
        script=SCRIPT,
        request_code="REQ-1",
        partition_where="ingestion_day = 2026-08-01",
        salt_label="proyecto-x",
    )
    base.update(kw)
    return repo.record(**base)


def test_record_and_get(repo):
    sid = _record(repo)
    entry = repo.get(sid)
    assert entry["script"] == SCRIPT
    assert entry["status"] == STATUS_OK
    assert entry["request_code"] == "REQ-1"
    assert entry["at"]


def test_script_guardado_no_lleva_salts_reales(repo):
    """La BD es compartida: el script solo puede llevar placeholders."""
    entry = repo.get(_record(repo))
    assert masking.TEXT_SALT_PLACEHOLDER in entry["script"]
    assert "saltReal" not in entry["script"]


def test_listado_mas_reciente_primero(repo):
    _record(repo, dest_table="p.uno")
    _record(repo, dest_table="p.dos")
    assert [e["dest_table"] for e in repo.list_scripts()] == ["p.dos", "p.uno"]


def test_busqueda_por_tabla_y_solicitud(repo):
    _record(repo, dest_table="p.clientes_enm", request_code="REQ-1")
    _record(repo, dest_table="p.ventas_enm", request_code="REQ-2", origin=ORIGIN_ADHOC)
    assert len(repo.list_scripts(search="clientes")) == 1
    assert len(repo.list_scripts(search="REQ-2")) == 1
    assert len(repo.list_scripts(search="nada")) == 0


def test_registra_los_fallos(repo):
    entry = repo.get(_record(repo, status=STATUS_ERROR, error="tabla no existe"))
    assert entry["status"] == STATUS_ERROR
    assert entry["error"] == "tabla no existe"
