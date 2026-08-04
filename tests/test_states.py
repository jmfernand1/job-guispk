"""Tests de la maquina de estados (sin BD)."""

import pytest

from core import states


def test_valid_transitions_by_role():
    states.validate_transition(states.BORRADOR, states.ENVIADA, states.ROLE_ALIADO)
    states.validate_transition(states.ENVIADA, states.BORRADOR, states.ROLE_ALIADO)
    states.validate_transition(states.RECHAZADA, states.BORRADOR, states.ROLE_ALIADO)
    states.validate_transition(states.ENVIADA, states.APROBADA, states.ROLE_INTERNO)
    states.validate_transition(states.ENVIADA, states.RECHAZADA, states.ROLE_INTERNO)
    states.validate_transition(states.APROBADA, states.EJECUTADA, states.ROLE_INTERNO)


def test_unknown_transition_raises():
    with pytest.raises(states.TransitionError):
        states.validate_transition(states.BORRADOR, states.EJECUTADA, states.ROLE_INTERNO)
    with pytest.raises(states.TransitionError):
        states.validate_transition(states.EJECUTADA, states.BORRADOR, states.ROLE_ALIADO)


def test_wrong_role_raises():
    # el aliado no aprueba ni ejecuta
    with pytest.raises(states.TransitionError):
        states.validate_transition(states.ENVIADA, states.APROBADA, states.ROLE_ALIADO)
    with pytest.raises(states.TransitionError):
        states.validate_transition(states.APROBADA, states.EJECUTADA, states.ROLE_ALIADO)
    # el interno no envia solicitudes del aliado
    with pytest.raises(states.TransitionError):
        states.validate_transition(states.BORRADOR, states.ENVIADA, states.ROLE_INTERNO)
