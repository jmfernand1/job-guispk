"""Serializacion de campos y helpers comunes a los repos."""

import json
import uuid
from datetime import datetime, timezone


def utcnow_iso() -> str:
    """Timestamp ISO-8601 en UTC, segundos enteros (auditable y ordenable)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    """Id unico ordenable por tiempo (microsegundos UTC + azar).

    El fold de eventos ordena por (at, event_id) y `at` tiene precision de
    segundos: el prefijo temporal del id desempata eventos del mismo segundo
    en orden de insercion (misma maquina; entre maquinas queda el clock skew,
    riesgo aceptado y documentado en la decision 006).
    """
    now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return f"{now}{uuid.uuid4().hex[:12]}"


def fields_to_json(fields) -> str:
    """[{col, type, masking?}] -> JSON canonico (claves ordenadas, estable).

    Sin `masking` es lo que pide el aliado; con `masking`, la decision del
    interno. El orden canonico importa: el interno regenera y compara.
    """
    return json.dumps(fields, ensure_ascii=False, sort_keys=True)


def fields_from_json(raw: str):
    return json.loads(raw)


def columns_to_json(columns) -> str:
    """[(name, type)] o [{name, type}] -> JSON de [{name, type}]."""
    norm = []
    for c in columns:
        if isinstance(c, dict):
            norm.append({"name": c["name"], "type": c["type"]})
        else:
            norm.append({"name": c[0], "type": c[1]})
    return json.dumps(norm, ensure_ascii=False)


def columns_from_json(raw: str):
    """JSON -> [(name, type)] listo para ColumnTable.load_columns."""
    return [(c["name"], c["type"]) for c in json.loads(raw)]
