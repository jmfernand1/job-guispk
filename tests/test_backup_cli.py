"""El CLI de respaldo: resolucion de ruta, modos y codigo de salida.

La conexion a Sparky se stubea; lo que se prueba es el contrato con el
agendador (que hace, que informa, con que codigo termina).
"""

import pytest

from core.store import backup as backup_mod
from tests.fake_impala import new_runner_with_schema
from tools import backup_guispk


@pytest.fixture
def runner():
    return new_runner_with_schema()


@pytest.fixture
def cli(monkeypatch, runner, tmp_path):
    """CLI con Sparky stubeado y config vacia; devuelve la ruta del .db."""
    monkeypatch.setattr(backup_guispk, "_connect", lambda settings: runner)
    monkeypatch.setattr(
        backup_guispk, "resolve_settings",
        lambda: {"dsn": "D", "schema": "proceso_enmascarado", "backup_db": ""},
    )
    return str(tmp_path / "guispk_backup.db")


def _poblar(runner):
    from core.store.catalog_repo import CatalogRepo

    CatalogRepo(runner).add_table("zona.clientes", "maestro", "interno1")


def test_backup_escribe_el_db(cli, runner, capsys):
    _poblar(runner)
    assert backup_guispk.main(["--db", cli]) == 0
    assert "1 filas" in capsys.readouterr().out
    assert backup_mod.summary(cli)["inventory_events"] == 1


def test_restore_recrea_el_esquema_y_repuebla(cli, runner, monkeypatch, capsys):
    """Tras un DROP no hay tablas: el CLI las crea antes de restaurar."""
    _poblar(runner)
    backup_guispk.main(["--db", cli])

    vacio = new_runner_with_schema()
    monkeypatch.setattr(backup_guispk, "_connect", lambda settings: vacio)
    assert backup_guispk.main(["--db", cli, "--restore"]) == 0
    assert "1 filas insertadas" in capsys.readouterr().out


def test_summary_no_toca_impala(cli, runner, monkeypatch, capsys):
    _poblar(runner)
    backup_guispk.main(["--db", cli])

    def _explota(settings):
        raise AssertionError("--summary no deberia conectarse")

    monkeypatch.setattr(backup_guispk, "_connect", _explota)
    assert backup_guispk.main(["--db", cli, "--summary"]) == 0
    assert "filas en total" in capsys.readouterr().out


def test_sin_ruta_falla_con_mensaje(cli, capsys):
    with pytest.raises(SystemExit, match="Falta la ruta"):
        backup_guispk.main([])


def test_usa_backup_db_de_la_config(monkeypatch, runner, tmp_path):
    """Agendado no se pasa --db: la ruta sale del config.ini."""
    destino = str(tmp_path / "desde-config.db")
    monkeypatch.setattr(backup_guispk, "_connect", lambda settings: runner)
    monkeypatch.setattr(
        backup_guispk, "resolve_settings",
        lambda: {"dsn": "D", "schema": "proceso_enmascarado", "backup_db": destino},
    )
    assert backup_guispk.main([]) == 0
    assert backup_mod.summary(destino) is not None


def test_error_devuelve_codigo_1(cli, monkeypatch, capsys):
    """El agendador solo mira el codigo de salida: un fallo no puede dar 0."""
    def _falla(settings):
        raise RuntimeError("Sparky rechazo el login")

    monkeypatch.setattr(backup_guispk, "_connect", _falla)
    assert backup_guispk.main(["--db", cli]) == 1
    assert "Sparky rechazo el login" in capsys.readouterr().err


def test_restore_y_summary_son_excluyentes(cli):
    with pytest.raises(SystemExit):
        backup_guispk.main(["--db", cli, "--restore", "--summary"])
