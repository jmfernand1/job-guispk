"""Repositorio de solicitudes: ciclo de vida, items y auditoria.

Las transiciones usan concurrencia optimista (UPDATE ... WHERE state = <actual>):
si otro proceso movio la solicitud primero, la transicion falla limpio con
TransitionError en lugar de pisar el estado.
"""

import sqlite3
from datetime import datetime

from core import models, states
from core.store.db import open_db


class RequestsRepo:
    def __init__(self, db_path: str):
        self._path = db_path

    # -- creacion ------------------------------------------------------------
    def create_request(self, requester: str) -> dict:
        """Crea una solicitud en borrador con codigo REQ-YYYYMMDD-NNN."""
        today = datetime.now().strftime("%Y%m%d")
        with open_db(self._path) as con:
            for _ in range(20):  # reintenta si otro proceso tomo el consecutivo
                n = con.execute(
                    "SELECT COUNT(*) FROM requests WHERE code LIKE ?",
                    (f"REQ-{today}-%",),
                ).fetchone()[0]
                code = f"REQ-{today}-{n + 1:03d}"
                try:
                    cur = con.execute(
                        "INSERT INTO requests (code, state, requester, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (code, states.BORRADOR, requester, models.utcnow_iso()),
                    )
                    self._audit(con, cur.lastrowid, requester, "crear", code)
                    return {"id": cur.lastrowid, "code": code}
                except sqlite3.IntegrityError:
                    continue
            raise RuntimeError("No se pudo generar un codigo de solicitud unico.")

    def add_item(
        self,
        request_id: int,
        src_table: str,
        dest_table: str,
        schema_capture_id,
        fields,
        partition_where_requested,
        sql_preview: str,
    ) -> int:
        with open_db(self._path) as con:
            cur = con.execute(
                "INSERT INTO request_items (request_id, src_table, dest_table, "
                "schema_capture_id, fields_json, partition_where_requested, sql_preview) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id,
                    src_table,
                    dest_table,
                    schema_capture_id,
                    models.fields_to_json(fields),
                    partition_where_requested,
                    sql_preview,
                ),
            )
            return cur.lastrowid

    # -- consulta ------------------------------------------------------------
    def list_requests(self, state=None, requester=None):
        query = "SELECT * FROM requests"
        clauses, params = [], []
        if state:
            clauses.append("state = ?")
            params.append(state)
        if requester:
            clauses.append("requester = ?")
            params.append(requester)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC"
        with open_db(self._path) as con:
            return [dict(r) for r in con.execute(query, params)]

    def get_request(self, request_id: int):
        """Solicitud + sus items (fields ya deserializados)."""
        with open_db(self._path) as con:
            row = con.execute(
                "SELECT * FROM requests WHERE id = ?", (request_id,)
            ).fetchone()
            if row is None:
                return None
            req = dict(row)
            req["items"] = []
            for item in con.execute(
                "SELECT * FROM request_items WHERE request_id = ? ORDER BY id",
                (request_id,),
            ):
                d = dict(item)
                d["fields"] = models.fields_from_json(d["fields_json"])
                req["items"].append(d)
            return req

    def audit_trail(self, request_id: int):
        with open_db(self._path) as con:
            return [
                dict(r)
                for r in con.execute(
                    "SELECT * FROM audit_log WHERE request_id = ? ORDER BY id",
                    (request_id,),
                )
            ]

    # -- transiciones --------------------------------------------------------
    def transition(
        self,
        request_id: int,
        to_state: str,
        who: str,
        role: str,
        comment=None,
        execution_log=None,
        executed_partition_where=None,
    ):
        """Mueve la solicitud validando maquina de estados, rol y concurrencia."""
        now = models.utcnow_iso()
        with open_db(self._path) as con:
            row = con.execute(
                "SELECT state FROM requests WHERE id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise states.TransitionError(f"Solicitud {request_id} no existe.")
            from_state = row["state"]
            states.validate_transition(from_state, to_state, role)

            sets = ["state = ?"]
            params = [to_state]
            if to_state == states.ENVIADA:
                sets.append("sent_at = ?")
                params.append(now)
            elif to_state == states.RECHAZADA:
                sets += ["reviewed_by = ?", "reviewed_at = ?", "review_comment = ?"]
                params += [who, now, comment]
            elif to_state == states.EJECUTADA:
                sets += [
                    "executed_by = ?",
                    "executed_at = ?",
                    "execution_log = ?",
                    "executed_partition_where = ?",
                ]
                params += [who, now, execution_log, executed_partition_where]
                if from_state == states.ENVIADA:
                    # Sin paso de aprobacion: quien ejecuta es quien revisa.
                    sets += ["reviewed_by = ?", "reviewed_at = ?", "review_comment = ?"]
                    params += [who, now, comment]

            params += [request_id, from_state]
            cur = con.execute(
                f"UPDATE requests SET {', '.join(sets)} WHERE id = ? AND state = ?",
                params,
            )
            if cur.rowcount == 0:
                raise states.TransitionError(
                    "Otro usuario modifico la solicitud al mismo tiempo. "
                    "Refresca e intenta de nuevo."
                )
            detail = f"{from_state} -> {to_state}"
            if comment:
                detail += f" | {comment}"
            self._audit(con, request_id, who, "transicion", detail)

    # -- auditoria -----------------------------------------------------------
    @staticmethod
    def _audit(con, request_id, who, action, detail=None):
        con.execute(
            "INSERT INTO audit_log (request_id, at, who, action, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (request_id, models.utcnow_iso(), who, action, detail),
        )
