"""Repositorio de solicitudes sobre Impala: ciclo de vida append-only.

Impala/Parquet no soporta UPDATE, asi que no hay bloqueo optimista por
UPDATE...WHERE: cada cambio es un INSERT en guispk_request_events y el estado
vigente es el *fold* de los eventos en orden (event_at, event_id). El fold ignora
eventos cuyo from_state no coincide con el estado plegado hasta ese punto;
tras insertar, el repo re-pliega y, si su evento quedo ignorado (otro proceso
gano la carrera), lanza TransitionError — el mismo contrato que tenia el
UPDATE con rowcount=0 sobre SQLite.

Los eventos son tambien la auditoria: audit_trail() se deriva de aqui.
"""

import uuid
from datetime import datetime

from core import models, states
from core.store import ddl

_EV_CREAR = "crear"
_EV_TRANSICION = "transicion"
_EV_DECISION = "decision_enmascaramiento"

# Nombres fisicos de columna: ninguno puede ser palabra reservada de Impala
# (por eso event_at/event_by/actor_role/note y no at/who/role/comment). Los
# parametros Python de los metodos publicos siguen llamandose who/role/comment.
_EVENT_COLS = (
    "event_id, request_id, code, event_at, event_by, actor_role, event_type, "
    "from_state, to_state, requester, item_id, fields_final_json, note, "
    "execution_log, executed_partition_where"
)


def _sort_events(rows):
    return sorted(rows, key=lambda e: (e["event_at"] or "", e["event_id"]))


def fold_events(events):
    """Pliega los eventos (ya ordenados) de UNA solicitud.

    Devuelve (req, decisions, applied_ids):
    - req: dict con las columnas que antes vivian en la tabla requests
      (state, code, requester, created_at, sent_at, reviewed_*, executed_*...)
      o None si no hay evento 'crear'.
    - decisions: item_id -> fields_final_json (ultima decision valida gana).
    - applied_ids: event_ids que el fold acepto (los demas perdieron carreras).
    """
    req = None
    decisions = {}
    applied = set()
    for e in events:
        etype = e["event_type"]
        if etype == _EV_CREAR:
            if req is not None:
                continue
            req = {
                "id": e["request_id"],
                "code": e["code"],
                "state": states.BORRADOR,
                "requester": e["requester"],
                "created_at": e["event_at"],
                "sent_at": None,
                "reviewed_by": None,
                "reviewed_at": None,
                "review_comment": None,
                "executed_by": None,
                "executed_at": None,
                "execution_log": None,
                "executed_partition_where": None,
            }
            applied.add(e["event_id"])
        elif etype == _EV_TRANSICION:
            if req is None or e["from_state"] != req["state"]:
                continue
            if (e["from_state"], e["to_state"]) not in states.TRANSITIONS:
                continue
            to_state = e["to_state"]
            req["state"] = to_state
            if to_state == states.ENVIADA:
                req["sent_at"] = e["event_at"]
            elif to_state == states.RECHAZADA:
                req["reviewed_by"] = e["event_by"]
                req["reviewed_at"] = e["event_at"]
                req["review_comment"] = e["note"]
            elif to_state == states.EJECUTADA:
                req["executed_by"] = e["event_by"]
                req["executed_at"] = e["event_at"]
                req["execution_log"] = e["execution_log"]
                req["executed_partition_where"] = e["executed_partition_where"]
                # Sin paso de aprobacion: quien ejecuta es quien revisa.
                req["reviewed_by"] = e["event_by"]
                req["reviewed_at"] = e["event_at"]
                req["review_comment"] = e["note"]
            applied.add(e["event_id"])
        elif etype == _EV_DECISION:
            # Solo vale mientras la solicitud sigue enviada (misma regla que
            # tenia el UPDATE guardado por estado en SQLite).
            if req is None or req["state"] != states.ENVIADA:
                continue
            decisions[e["item_id"]] = e["fields_final_json"]
            applied.add(e["event_id"])
    return req, decisions, applied


class RequestsRepo:
    def __init__(self, runner, schema: str = ddl.DEFAULT_SCHEMA):
        self._runner = runner
        self._events_t = ddl.qname("request_events", schema)
        self._items_t = ddl.qname("request_items", schema)

    # -- eventos -------------------------------------------------------------
    def _events_for(self, request_id: str):
        rows = self._runner.query(
            f"SELECT * FROM {self._events_t} WHERE request_id = ?",
            (request_id,),
        )
        return _sort_events(rows)

    def _insert_event(self, **values) -> str:
        event_id = models.new_id()
        row = {
            "event_id": event_id,
            "request_id": None,
            "code": None,
            "event_at": models.utcnow_iso(),
            "event_by": None,
            "actor_role": None,
            "event_type": None,
            "from_state": None,
            "to_state": None,
            "requester": None,
            "item_id": None,
            "fields_final_json": None,
            "note": None,
            "execution_log": None,
            "executed_partition_where": None,
        }
        unknown = set(values) - set(row)
        if unknown:
            # Sin esto un nombre viejo (who, role, comment...) se colaba como
            # clave extra y el INSERT fallaba con "N values for 15 columns".
            raise ValueError(f"Columnas inexistentes en el evento: {sorted(unknown)}")
        row.update(values)
        placeholders = ", ".join("?" for _ in row)
        self._runner.execute(
            f"INSERT INTO {self._events_t} ({_EVENT_COLS}) "
            f"VALUES ({placeholders})",
            tuple(row.values()),
        )
        return event_id

    # -- creacion ------------------------------------------------------------
    def create_request(self, requester: str) -> dict:
        """Crea una solicitud en borrador con codigo REQ-YYYYMMDD-XXXX.

        El sufijo es aleatorio (uuid): sin constraints UNIQUE en Impala, un
        consecutivo seria una carrera permanente entre maquinas.
        """
        request_id = models.new_id()
        today = datetime.now().strftime("%Y%m%d")
        code = f"REQ-{today}-{uuid.uuid4().hex[:4].upper()}"
        self._insert_event(
            request_id=request_id,
            code=code,
            event_by=requester,
            actor_role=states.ROLE_ALIADO,
            event_type=_EV_CREAR,
            to_state=states.BORRADOR,
            requester=requester,
        )
        return {"id": request_id, "code": code}

    def add_item(
        self,
        request_id: str,
        src_table: str,
        dest_table: str,
        schema_capture_id,
        fields,
        partition_where_requested,
        sql_preview: str,
    ) -> str:
        item_id = models.new_id()
        self._runner.execute(
            f"INSERT INTO {self._items_t} (item_id, request_id, src_table, "
            "dest_table, schema_capture_id, fields_json, "
            "partition_where_requested, sql_preview, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id,
                request_id,
                src_table,
                dest_table,
                schema_capture_id,
                models.fields_to_json(fields),
                partition_where_requested,
                sql_preview,
                models.utcnow_iso(),
            ),
        )
        return item_id

    # -- revision del interno ------------------------------------------------
    def set_item_final_fields(self, item_id: str, fields, who: str):
        """Registra el enmascaramiento que decidio el interno para un item.

        Solo vale sobre una solicitud enviada: si otro interno la ejecuto o
        rechazo (antes o durante), falla limpio con TransitionError.
        """
        rows = self._runner.query(
            f"SELECT request_id FROM {self._items_t} WHERE item_id = ?",
            (item_id,),
        )
        if not rows:
            raise states.TransitionError(f"El item {item_id} no existe.")
        request_id = rows[0]["request_id"]

        req, _, _ = fold_events(self._events_for(request_id))
        if req is None or req["state"] != states.ENVIADA:
            raise states.TransitionError(
                "La solicitud ya no esta enviada: otro usuario la ejecuto o "
                "la rechazo. Refresca e intenta de nuevo."
            )
        event_id = self._insert_event(
            request_id=request_id,
            event_by=who,
            actor_role=states.ROLE_INTERNO,
            event_type=_EV_DECISION,
            item_id=item_id,
            fields_final_json=models.fields_to_json(fields),
        )
        _, _, applied = fold_events(self._events_for(request_id))
        if event_id not in applied:
            raise states.TransitionError(
                "La solicitud ya no esta enviada: otro usuario la ejecuto o "
                "la rechazo. Refresca e intenta de nuevo."
            )

    # -- consulta ------------------------------------------------------------
    def _fold_all(self):
        rows = self._runner.query(f"SELECT * FROM {self._events_t}")
        by_request = {}
        for row in rows:
            by_request.setdefault(row["request_id"], []).append(row)
        folded = []
        for events in by_request.values():
            req, decisions, _ = fold_events(_sort_events(events))
            if req is not None:
                folded.append((req, decisions))
        return folded

    def list_requests(self, state=None, requester=None):
        result = [
            req
            for req, _ in self._fold_all()
            if (not state or req["state"] == state)
            and (not requester or req["requester"] == requester)
        ]
        result.sort(key=lambda r: (r["created_at"] or "", r["id"]), reverse=True)
        return result

    def get_request(self, request_id: str):
        """Solicitud + sus items (fields ya deserializados)."""
        events = self._events_for(request_id)
        req, decisions, _ = fold_events(events)
        if req is None:
            return None
        req["items"] = []
        items = self._runner.query(
            f"SELECT * FROM {self._items_t} WHERE request_id = ?",
            (request_id,),
        )
        items.sort(key=lambda i: (i["created_at"] or "", i["item_id"]))
        for item in items:
            d = dict(item)
            d["id"] = d["item_id"]
            d["fields"] = models.fields_from_json(d["fields_json"])
            final_raw = decisions.get(d["item_id"])
            d["fields_final_json"] = final_raw
            d["fields_final"] = (
                models.fields_from_json(final_raw) if final_raw else None
            )
            req["items"].append(d)
        return req

    def audit_trail(self, request_id: str):
        """Auditoria derivada de los eventos (los eventos SON la auditoria).

        Solo incluye eventos aplicados por el fold: un evento que perdio una
        carrera nunca fue una accion real, igual que el audit_log de SQLite
        solo registraba operaciones que llegaron a confirmarse.
        """
        events = self._events_for(request_id)
        _, _, applied = fold_events(events)
        trail = []
        for e in events:
            if e["event_id"] not in applied:
                continue
            etype = e["event_type"]
            if etype == _EV_CREAR:
                detail = e["code"]
            elif etype == _EV_TRANSICION:
                detail = f"{e['from_state']} -> {e['to_state']}"
                if e["note"]:
                    detail += f" | {e['note']}"
            else:
                fields = models.fields_from_json(e["fields_final_json"])
                detail = ", ".join(
                    f"{f['col']}:{f.get('masking')}" for f in fields
                )
            trail.append(
                {
                    "request_id": e["request_id"],
                    "event_at": e["event_at"],
                    "event_by": e["event_by"],
                    "action": etype,
                    "detail": detail,
                }
            )
        return trail

    # -- transiciones --------------------------------------------------------
    def transition(
        self,
        request_id: str,
        to_state: str,
        who: str,
        role: str,
        comment=None,
        execution_log=None,
        executed_partition_where=None,
    ):
        """Mueve la solicitud validando maquina de estados, rol y concurrencia."""
        events = self._events_for(request_id)
        req, _, _ = fold_events(events)
        if req is None:
            raise states.TransitionError(f"Solicitud {request_id} no existe.")
        from_state = req["state"]
        states.validate_transition(from_state, to_state, role)

        event_id = self._insert_event(
            request_id=request_id,
            code=req["code"],
            event_by=who,
            actor_role=role,
            event_type=_EV_TRANSICION,
            from_state=from_state,
            to_state=to_state,
            note=comment,
            execution_log=execution_log,
            executed_partition_where=executed_partition_where,
        )
        _, _, applied = fold_events(self._events_for(request_id))
        if event_id not in applied:
            raise states.TransitionError(
                "Otro usuario modifico la solicitud al mismo tiempo. "
                "Refresca e intenta de nuevo."
            )
