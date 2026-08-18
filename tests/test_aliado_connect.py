"""El aliado prefiere Sparky y cae a ODBC solo si Sparky falla."""

import pytest

from aliado import impala_client


class _FakeOdbcRunner:
    pass


@pytest.fixture
def sin_odbc(monkeypatch):
    """ODBC que explota: sirve para probar que ni se intento."""

    def _boom(dsn, username, password):
        raise AssertionError("no deberia haber intentado ODBC")

    monkeypatch.setattr(impala_client.ImpalaOdbcRunner, "connect", _boom)


@pytest.fixture
def odbc_ok(monkeypatch):
    monkeypatch.setattr(
        impala_client.ImpalaOdbcRunner,
        "connect",
        lambda dsn, username, password: _FakeOdbcRunner(),
    )


def test_usa_sparky_cuando_conecta(sin_odbc):
    runner, backend, error = impala_client.connect_impala(
        "DSN", "u", "p", sparky_connect=lambda *a: "runner-sparky"
    )
    assert (runner, backend, error) == ("runner-sparky", impala_client.BACKEND_SPARKY, None)


def test_cae_a_odbc_si_sparky_falla(odbc_ok):
    def _sparky_falla(*args):
        raise RuntimeError("login rechazado")

    runner, backend, error = impala_client.connect_impala(
        "DSN", "u", "p", sparky_connect=_sparky_falla
    )
    assert isinstance(runner, _FakeOdbcRunner)
    assert backend == impala_client.BACKEND_ODBC
    assert "login rechazado" in error  # el aliado ve por que quedo en ODBC


def test_cae_a_odbc_si_no_hay_sparky_instalado(odbc_ok):
    """El caso del aliado sin sparky_bc: ImportError, no crash."""

    def _sin_libreria(*args):
        raise ImportError("No module named 'sparky_bc'")

    _, backend, error = impala_client.connect_impala(
        "DSN", "u", "p", sparky_connect=_sin_libreria
    )
    assert backend == impala_client.BACKEND_ODBC
    assert "sparky_bc" in error


def test_si_fallan_los_dos_propaga_el_error_de_odbc(monkeypatch):
    def _odbc_falla(dsn, username, password):
        raise RuntimeError("DSN no configurado")

    monkeypatch.setattr(impala_client.ImpalaOdbcRunner, "connect", _odbc_falla)
    with pytest.raises(RuntimeError, match="DSN no configurado"):
        impala_client.connect_impala(
            "DSN", "u", "p", sparky_connect=lambda *a: (_ for _ in ()).throw(OSError())
        )
