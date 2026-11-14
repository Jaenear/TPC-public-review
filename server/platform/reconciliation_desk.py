"""Operator review and safe read projections, separate from financial effects."""

from datetime import datetime, timezone
import json
import re

from ..core import ApiError, new_id
from .common import audit
from .reconciliation_contract import document, hashed, opaque
from .reconciliation_queue import at
from .reconciliation_desk_facts import ORDER_CTE, aligned, order_view, parameters
from .reconciliation_desk_events import EVENT_CTE, event_view
from .reconciliation_desk_privacy import checked_note, scrub
from .reconciliation_desk_queue import queue, queue_filters
from .reconciliation_desk_timeline import timeline

ACTIONS = frozenset(("claim", "takeover", "release", "note", "investigate", "resolve", "reopen"))


def case_view(row, actor_id):
    if not row["case_id"]:
        return {"id": None, "version": 0, "state": "unclaimed", "owner": None, "updatedAt": None, "changed": False, "ownedByMe": False}
    owner = {"id": row["owner_id"], "name": row["owner_name"], "active": row["owner_status"] == "active" and row["owner_role"] == "admin"} if row["owner_id"] else None
    return {"id": row["case_id"], "version": row["case_version"], "state": row["case_state"], "owner": owner,
            "updatedAt": row["case_updated"], "changed": bool(row["review_changed"]), "ownedByMe": row["owner_id"] == actor_id}


class ReconciliationDesk:
    def __init__(self, app, runtime):
        self.app, self.runtime = app, runtime

    def _parameters(self, c):
        stamp = at(self.runtime.clock)
        observed = datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        return parameters(c, self.runtime.scope(), stamp, observed)

    def _operator(self, c, user):
        return self.runtime.model._operator(c, user)

    def _subject(self, c, kind, sid, params):
        if kind not in ("order", "event") or not isinstance(sid, str) or not re.fullmatch(r"[a-z0-9_-]{1,120}", sid):
            raise ApiError(404, "현재 대사 기록을 찾을 수 없습니다.")
        sql = ORDER_CTE+(EVENT_CTE if kind == "event" else "")+" SELECT * FROM "+("event_projected" if kind == "event" else "projected")+" WHERE id=:subject"
        row = c.execute(sql, {**params, "subject": sid}).fetchone()
        if not row:
            raise ApiError(404, "현재 대사 기록을 찾을 수 없습니다.")
        value = order_view(row) if kind == "order" else event_view(row)
        if kind == "event":
            linked = self._subject(c, "order", row["linked_order"], params)[0] if row["linked_order"] else None
            value["financialAligned"] = bool(linked and row["algorithm"] and row["disposition"] == "eligible" and not row["conflict_count"] and row["scope_matches"] and aligned(linked))
        return row, value

    def _secrets(self, c, kind, sid):
        if kind == "order":
            row = c.execute("SELECT payment_key FROM purchase_orders WHERE id=?", (sid,)).fetchone()
        else:
            row = c.execute("SELECT o.payment_key FROM provider_event_links l JOIN purchase_orders o ON o.id=l.order_id WHERE l.event_id=?", (sid,)).fetchone()
        gateway = self.runtime.commerce.gateway
        return tuple(v for v in [row[0] if row else None, *(getattr(gateway, key, None) for key in ("secret_key", "lookup_secret_key", "client_key", "merchant_id"))] if isinstance(v, str) and v)

    def _review_scope(self, row, value):
        gateway, scope = self.runtime.commerce.gateway, self.runtime.scope()
        if gateway.ready and gateway.mode in ("test", "live") and row["mode"] != gateway.mode:
            raise ApiError(409, "원래 기록과 같은 결제 환경에서 검토해주세요.")
        # An unconfigured provider does not stop internal incident notes. A
        # known, different merchant is never adopted by an operator action.
        if scope and ((value["kind"] == "event" and not row["scope_matches"]) or (value["kind"] == "order" and row["scope_recorded"] and not row["scope_matches"])):
            raise ApiError(409, "현재와 다른 상점 범위의 검토는 원래 환경에서 진행해주세요.")

    def _query_control(self, row):
        scope = self.runtime.scope()
        gateway = self.runtime.commerce.gateway
        by_key = row["current_key_hash"] is not None
        supported = callable(getattr(gateway, "lookup_payment", None)) if by_key else bool(callable(getattr(gateway, "can_lookup_order", None)) and gateway.can_lookup_order())
        blockers = []
        if not scope: blockers.append("configuration")
        elif scope.mode != row["mode"] or (row["scope_recorded"] and not row["scope_matches"]): blockers.append("scope_mismatch")
        if row["active_count"]: blockers.append("active_work")
        if not supported: blockers.append("lookup_unavailable")
        return {"available": not blockers, "blockers": blockers, "requiresScopeAcknowledgment": not bool(row["scope_recorded"]), "lookupBy": "payment" if by_key else "order"}

    def _detail(self, c, actor, kind, sid, query, params):
        row, value = self._subject(c, kind, sid, params)
        review = case_view(row, actor["id"])
        try:
            self._review_scope(row, value)
            can_review = True
        except ApiError:
            can_review = False
        if not review["owner"]:
            actions = ["claim"] if review["state"] != "resolved" else []
        elif not review["ownedByMe"]:
            actions = ["takeover"]
        elif review["state"] == "resolved":
            actions = ["reopen"]
        else:
            actions = ["note", "investigate", "release"]
            if value["financialAligned"] and not (kind == "order" and row["receipt_conflicts"]): actions.append("resolve")
        secrets = self._secrets(c, kind, sid)
        value["case"] = {**review, "allowedActions": actions if can_review else [], "reviewAllowed": can_review}
        if kind == "order": value["queryControl"] = self._query_control(row)
        return {"schemaVersion": 1, "viewerId": actor["id"], "observedAt": params["stamp"], "configuration": self.runtime.describe(),
                "subject": value, "timeline": timeline(c, kind, sid, query, lambda text: scrub(text, secrets))}

    def read(self, user, kind, sid, query):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor = self._operator(c, user)
            return self._detail(c, actor, kind, sid, query, self._parameters(c))

    def list(self, user, query):
        filters = queue_filters(query)
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor = self._operator(c, user)
            params = self._parameters(c)
            value = queue(c, filters, params, actor["id"], case_view)
            return {"schemaVersion": 1, "viewerId": actor["id"], "observedAt": params["stamp"], "configuration": self.runtime.describe(), **value}

    def _command(self, c, actor, key, kind, sid, request_hash=None):
        row = c.execute("SELECT * FROM reconciliation_case_commands WHERE actor_id=? AND key_hash=?", (actor["id"], key)).fetchone()
        if row and (row["subject_kind"] != kind or row["subject_id"] != sid or (request_hash is not None and row["request_hash"] != request_hash)):
            raise ApiError(409, "같은 검토 요청 번호의 대상이나 내용이 다릅니다. 원래 저장 결과부터 확인해주세요.")
        return row

    def _receipt(self, c, command):
        event = c.execute("SELECT id,case_id,version,action,created_at FROM reconciliation_case_events WHERE id=?", (command["event_id"],)).fetchone()
        return {"id": event["id"], "caseId": event["case_id"], "version": event["version"], "action": event["action"],
                "createdAt": event["created_at"], "subjectKind": command["subject_kind"], "subjectId": command["subject_id"]}

    def _transition(self, prior, actor_id, action, financial_aligned):
        if prior is None:
            if action != "claim": raise ApiError(409, "현재 기록에서 먼저 담당 시작을 남겨주세요.")
            return "open", actor_id
        state, owner = prior["state"], prior["owner_id"]
        if action == "takeover":
            if owner is None or owner == actor_id: raise ApiError(409, "현재 담당을 확인한 뒤 인계를 선택해주세요.")
            return state, actor_id
        if action == "claim":
            if owner is not None or state == "resolved": raise ApiError(409, "이미 담당이 있거나 검토가 종결됐습니다. 현재 기록을 다시 확인해주세요.")
            return state, actor_id
        if owner != actor_id: raise ApiError(409, "현재 담당만 기록을 수정할 수 있습니다. 인계가 필요하면 사유를 남겨주세요.")
        if action == "reopen":
            if state != "resolved": raise ApiError(409, "종결된 검토만 다시 시작할 수 있습니다.")
            return "open", actor_id
        if state == "resolved": raise ApiError(409, "종결 당시 근거는 보존됩니다. 먼저 검토를 다시 시작해주세요.")
        if action == "resolve" and not financial_aligned:
            raise ApiError(409, "최신 조회와 현재 주문·권한이 아직 일치하지 않습니다. 별도 조사로 기록해주세요.")
        return ({"note": state, "release": state, "investigate": "investigating", "resolve": "resolved"}[action], None if action == "release" else actor_id)

    def act(self, user, kind, sid, data):
        required = {"requestKey", "version", "contextHash", "action", "note"}
        if not isinstance(data, dict) or set(data) != required or not isinstance(data["action"], str) or data["action"] not in ACTIONS:
            raise ApiError(400, "검토 요청 번호·현재 판본/근거·행동·사유를 확인해주세요.")
        key = hashed(opaque(data["requestKey"], 120, 12))
        if type(data["version"]) is not int or data["version"] < 0 or not isinstance(data["contextHash"], str) or not re.fullmatch(r"[a-f0-9]{64}", data["contextHash"]):
            raise ApiError(400, "현재 검토 판본과 업무 근거를 다시 확인해주세요.")
        request_hash = hashed(document({"kind": kind, "id": sid, "body": data}))
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor = self._operator(c, user)
            self._subject(c, kind, sid, self._parameters(c))
            found = self._command(c, actor, key, kind, sid, request_hash)
            if found:
                return {"receipt": self._receipt(c, found), "replayed": True}
            checked_note(data["note"], self._secrets(c, kind, sid))
        self.app.limiter.check(("reconciliation-review", actor["id"]), 30, 60)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            actor = self._operator(c, user)
            params = self._parameters(c)
            row, value = self._subject(c, kind, sid, params)
            found = self._command(c, actor, key, kind, sid, request_hash)
            if found: return {"receipt": self._receipt(c, found), "replayed": True}
            note = checked_note(data["note"], self._secrets(c, kind, sid))
            self._review_scope(row, value)
            prior = c.execute("SELECT * FROM reconciliation_cases WHERE subject_kind=? AND COALESCE(order_id,event_id)=?", (kind, sid)).fetchone()
            version = prior["version"] if prior else 0
            if version != data["version"] or value["contextHash"] != data["contextHash"]:
                raise ApiError(409, "검토 또는 조회·주문·권한 근거가 바뀌었습니다. 현재 내용을 다시 비교해주세요.")
            can_resolve = value["financialAligned"] and not (kind == "order" and row["receipt_conflicts"])
            state, owner = self._transition(prior, actor["id"], data["action"], can_resolve)
            stamp, cid, eid = params["stamp"], prior["id"] if prior else new_id("recase"), new_id("recaseevent")
            if prior:
                c.execute("UPDATE reconciliation_cases SET owner_id=?,state=?,version=version+1,context_json=?,context_hash=?,updated_at=? WHERE id=?", (owner, state, row["facts_json"], value["contextHash"], stamp, cid))
            else:
                c.execute("INSERT INTO reconciliation_cases VALUES(?,?,?,?,?,?,1,?,?,?,?,?)", (cid, kind, sid if kind=="order" else None, sid if kind=="event" else None, owner, state, row["facts_json"], value["contextHash"], actor["id"], stamp, stamp))
            c.execute("INSERT INTO reconciliation_case_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (eid, cid, version+1, data["action"], prior["state"] if prior else None, state, prior["owner_id"] if prior else None, owner, actor["id"], note, value["contextHash"], stamp))
            c.execute("INSERT INTO reconciliation_case_commands VALUES(?,?,?,?,?,?,?,?,?,?)", (actor["id"], key, request_hash, cid, eid, kind, sid, version, value["contextHash"], stamp))
            audit(c, actor, "payment.review_recorded", "reconciliation_case", cid, {"eventId": eid, "action": data["action"], "version": version+1, "contextHash": value["contextHash"], "noteHash": hashed(note)})
            command = self._command(c, actor, key, kind, sid)
            return {"receipt": self._receipt(c, command), "replayed": False}

    def receipt(self, user, kind, sid, query):
        if set(query) != {"kind", "requestKey"} or any(len(v)!=1 for v in query.values()) or query["kind"][0] not in ("case", "query"):
            raise ApiError(400, "본인의 원 요청 종류와 번호를 확인해주세요.")
        key = hashed(opaque(query["requestKey"][0], 120, 12))
        command_kind = query["kind"][0]
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor = self._operator(c, user)
            self._subject(c, kind, sid, self._parameters(c))
            if command_kind == "case":
                command = self._command(c, actor, key, kind, sid)
                receipt = self._receipt(c, command) if command else None
                return {"schemaVersion": 1, "viewerId": actor["id"], "kind": "case", "subjectKind": kind, "subjectId": sid, "found": receipt is not None, "receipt": receipt}
            if kind != "order": raise ApiError(400, "수신 사건 자체에 금융 조회 요청을 만들 수 없습니다.")
            command = c.execute("SELECT * FROM reconciliation_commands WHERE actor_id=? AND request_key_hash=? AND order_id=?", (actor["id"], key, sid)).fetchone()
            value = self.runtime._command_result(c, command, True) if command else None
            if value: value["receipt"]["reason"] = scrub(value["receipt"]["reason"], self._secrets(c, kind, sid))
            return {"schemaVersion": 1, "viewerId": actor["id"], "kind": "query", "subjectKind": kind, "subjectId": sid, "found": value is not None,
                    "receipt": value["receipt"] if value else None, "job": value["job"] if value else None}

    def request_query(self, user, sid, data):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            self._operator(c, user)
            row, _ = self._subject(c, "order", sid, self._parameters(c))
            if not isinstance(data, dict) or "reason" not in data: raise ApiError(400, "현재 조회 사유와 원 요청을 확인해주세요.")
            checked_note(data["reason"], self._secrets(c, "order", sid), 1000)
            # Existing commands must replay even after their work advanced. New
            # intents are checked for current query capability before enqueue.
            key = hashed(opaque(data.get("requestKey"), 120, 12))
            existing = c.execute("SELECT 1 FROM reconciliation_commands WHERE actor_id=? AND request_key_hash=?", (user["id"], key)).fetchone()
            if not existing and not self._query_control(row)["available"]:
                raise ApiError(409, "현재 상점/조회 자격과 이미 진행 중인 작업을 먼저 확인해주세요.")
        return self.runtime.request_check(user, sid, data)

    def route(self, h, path, method, query):
        if path == "/api/manage/reconciliation/desk":
            if method != "GET": raise ApiError(405, "대사 목록은 조회로 제공합니다.")
            return 200, self.list(h.user, query), {}
        match = re.fullmatch(r"/api/manage/(?:purchases/([a-z0-9_-]+)/reconciliation/desk|reconciliation/desk/events/([a-z0-9_-]+))(?:/(actions|receipt|query))?", path)
        if not match: return None
        kind, sid, action = ("order" if match[1] else "event"), (match[1] or match[2]), match[3]
        if action is None and method == "GET": return 200, self.read(h.user, kind, sid, query), {}
        if action == "receipt" and method == "GET": return 200, self.receipt(h.user, kind, sid, query), {}
        if action in ("actions", "query") and method == "POST":
            if query: raise ApiError(400, "명시 검토/조회 요청에 페이지 필터를 붙일 수 없습니다.")
            if action == "query":
                if kind != "order": raise ApiError(400, "연결된 원 주문에서 조회해주세요.")
                value = self.request_query(h.user, sid, h.body())
            else: value = self.act(h.user, kind, sid, h.body())
            return (200 if value["replayed"] else 201), value, {}
        raise ApiError(405, "지원하는 대사 데스크 조회·명시 기록 방법을 확인해주세요.")
