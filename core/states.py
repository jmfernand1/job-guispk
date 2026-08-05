"""Maquina de estados de una solicitud de enmascaramiento.

borrador -> enviada -> ejecutada   (el interno ejecuta directo, sin aprobar)
                    -> rechazada -> borrador (corregir y reenviar)
enviada -> borrador (el aliado la retira)

'aprobada' es un estado heredado: ya no se puede entrar en el, pero las
solicitudes viejas que quedaron ahi todavia se pueden ejecutar o rechazar.
"""

BORRADOR = "borrador"
ENVIADA = "enviada"
APROBADA = "aprobada"  # heredado, no se genera mas
RECHAZADA = "rechazada"
EJECUTADA = "ejecutada"

ALL_STATES = (BORRADOR, ENVIADA, RECHAZADA, EJECUTADA)

# Estados desde los que el interno puede ejecutar una solicitud.
EJECUTABLES = (ENVIADA, APROBADA)

ROLE_ALIADO = "aliado"
ROLE_INTERNO = "interno"

# (desde, hacia) -> rol autorizado a ejecutar la transicion.
TRANSITIONS = {
    (BORRADOR, ENVIADA): ROLE_ALIADO,
    (ENVIADA, BORRADOR): ROLE_ALIADO,
    (ENVIADA, EJECUTADA): ROLE_INTERNO,
    (ENVIADA, RECHAZADA): ROLE_INTERNO,
    (RECHAZADA, BORRADOR): ROLE_ALIADO,
    # heredadas: solicitudes que quedaron en 'aprobada' antes del cambio.
    (APROBADA, EJECUTADA): ROLE_INTERNO,
    (APROBADA, RECHAZADA): ROLE_INTERNO,
}


class TransitionError(Exception):
    """Transicion invalida, no autorizada o perdida por concurrencia."""


def validate_transition(from_state: str, to_state: str, role: str):
    """Lanza TransitionError si la transicion no existe o el rol no puede hacerla."""
    allowed_role = TRANSITIONS.get((from_state, to_state))
    if allowed_role is None:
        raise TransitionError(
            f"Transicion invalida: {from_state} -> {to_state}."
        )
    if role != allowed_role:
        raise TransitionError(
            f"El rol '{role}' no puede pasar una solicitud de "
            f"{from_state} a {to_state} (requiere rol '{allowed_role}')."
        )
