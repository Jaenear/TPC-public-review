"""Explicit inquiry -> immutable review -> separate execution/recheck commands."""

import json
import re

from ..core import ApiError, new_id, now_iso, text_field
from .common import audit
from .refund_context import context_value
from .refund_case import CHECKS, actor_order, case_view, command, event, expected_version, intent, receipt


class RefundWorkflowService:
    def __init__(self, app, commerce):
        self.app = app
        self.commerce = commerce

    def projection(self, c, actor, order, operator, before=None):
        return case_view(c, actor, order, operator, self.commerce.config(), before)

    def read(self, user, oid, operator=False, before=None):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor, order = actor_order(c, user, oid, operator)
            return {"refundCase": self.projection(c, actor, order, operator, before)}

    def result(self, user, oid, saved, operator, replayed=False):
        value = self.read(user, oid, operator)
        return {**value, "receipt": saved, "replayed": replayed}

    def own_receipt(self, user, oid, key, operator=False):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor, order = actor_order(c, user, oid, operator)
            saved = command(c, actor, oid, key, operator=operator)
            if not saved:
                raise ApiError(404, "조회할 수 있는 요청 기록이 없습니다.")
            return {"receipt": saved, "refundCase": self.projection(c, actor, order, operator)}

    def inquire(self, user, oid, data):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            actor, order = actor_order(c, user, oid)
            request = intent(data, "inquiry", oid, {"reason"})
            reason = text_field(data, "reason", minimum=5, maximum=2000)
            saved = command(c, actor, oid, request["key"], request)
            replayed = saved is not None
            if not saved:
                expected_version(c, oid, data["expectedVersion"])
                if order["status"] != "paid":
                    raise ApiError(409, "결제 완료된 주문만 취소 문의를 접수할 수 있습니다.")
                old = c.execute("SELECT * FROM refund_requests WHERE order_id=?", (oid,)).fetchone()
                if old:
                    if old["reason"] != reason:
                        raise ApiError(409, "이미 접수한 문의가 있습니다. 원래 문의와 처리 결과를 확인해주세요.")
                else:
                    at = now_iso()
                    c.execute("INSERT INTO refund_requests(id,order_id,user_id,reason,created_at,updated_at) VALUES(?,?,?,?,?,?)", (new_id("refund"), oid, actor["id"], reason, at, at))
                    event(c, oid, "inquiry_submitted", actor["id"], reason)
                saved = receipt(c, actor, oid, request)
        return self.result(user, oid, saved, False, replayed)

    def review(self, user, oid, data):
        fields = {"decision", "contextHash", "checks", "basis", "operatorNote", "publicMessage", "cancelReason"}
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            actor, order = actor_order(c, user, oid, True)
            request = intent(data, "review", oid, fields)
            decision = data["decision"]
            checks = data["checks"]
            if decision not in ("full_cancel", "reply") or not isinstance(checks, list) or any(not isinstance(s, str) for s in checks) or len(checks) != len(CHECKS) or set(checks) != set(CHECKS):
                raise ApiError(400, "검토 방향과 각 확인 항목을 직접 선택해주세요.")
            basis = text_field(data, "basis", minimum=10, maximum=4000)
            note = text_field(data, "operatorNote", maximum=4000, optional=True)
            public = text_field(data, "publicMessage", minimum=5, maximum=2000)
            reason = text_field(data, "cancelReason", minimum=5, maximum=200, optional=decision == "reply")
            if decision == "reply" and reason:
                raise ApiError(400, "답변 검토에는 결제사 취소 사유를 지정하지 않습니다.")
            if not isinstance(data["contextHash"], str) or not re.fullmatch(r"[a-f0-9]{64}", data["contextHash"]):
                raise ApiError(400, "검토한 원래 기록의 식별자를 확인해주세요.")
            saved = command(c, actor, oid, request["key"], request, True)
            replayed = saved is not None
            if not saved:
                expected_version(c, oid, data["expectedVersion"])
                context = context_value(c, actor, order, True)
                if order["status"] != "paid" or c.execute("SELECT 1 FROM refund_operations WHERE order_id=?", (oid,)).fetchone():
                    raise ApiError(409, "현재 주문 상태에서는 새 검토를 저장할 수 없습니다.")
                if context["contextHash"] != data["contextHash"]:
                    raise ApiError(409, "확인한 문의·학습·수강권 기록이 바뀌었습니다. 다시 읽고 검토해주세요.")
                if decision == "reply" and (not context["inquiry"] or context["inquiry"]["status"] != "requested"):
                    raise ApiError(409, "확인 대기 중인 문의에만 답변 검토를 저장할 수 있습니다.")
                rid = new_id("refreview")
                c.execute("INSERT INTO refund_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (rid, oid, actor["id"], decision, context["contextHash"], json.dumps(context, ensure_ascii=False), data["expectedVersion"], order["version"], basis, note, public, reason, json.dumps(sorted(checks)), now_iso()))
                event(c, oid, "review_saved", actor["id"], "운영 검토를 저장했습니다. 아직 회원에게 전달하거나 취소를 요청하지 않았습니다.", review_id=rid)
                saved = receipt(c, actor, oid, request, review_id=rid)
                audit(c, actor, "refund.review_saved", "purchase", oid, {"reviewId": rid, "decision": decision, "contextHash": context["contextHash"]})
        return self.result(user, oid, saved, True, replayed)

    def review_detail(self, user, oid, rid):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor_order(c, user, oid, True)
            row = c.execute("SELECT * FROM refund_reviews WHERE id=? AND order_id=?", (rid, oid)).fetchone()
            if not row:
                raise ApiError(404, "검토 원본을 찾을 수 없습니다.")
            return {"reviewRecord": {"id": row["id"], "orderId": oid, "reviewerId": row["reviewer_id"], "decision": row["decision"], "basis": row["basis"], "operatorNote": row["operator_note"], "publicMessage": row["public_message"], "cancelReason": row["cancel_reason"], "checks": json.loads(row["checks_json"]), "context": json.loads(row["context_json"]), "createdAt": row["created_at"]}}

    def act(self, user, oid, data, expected_decision=None):
        from .refund_cancellation import perform

        return perform(self, user, oid, data, expected_decision)

    def route(self, h, path, method, query):
        match = re.fullmatch(r"/api/(checkout/orders|manage/purchases)/([a-z0-9_]+)/refund-case(?:/(inquiry|reviews|actions|receipts/([A-Za-z0-9_-]{12,120})|reviews/([a-z0-9_]+)))?", path)
        if not match:
            return None
        operator, oid, action = match.group(1) == "manage/purchases", match.group(2), match.group(3)
        if action is None and method == "GET":
            try:
                before = int(query["before"][0]) if "before" in query else None
            except (ValueError, TypeError, IndexError):
                raise ApiError(400, "이력 페이지를 확인해주세요.")
            return 200, self.read(h.user, oid, operator, before), {}
        if action and action.startswith("receipts/") and method == "GET":
            return 200, self.own_receipt(h.user, oid, match.group(4), operator), {}
        if action and action.startswith("reviews/") and operator and method == "GET":
            return 200, self.review_detail(h.user, oid, match.group(5)), {}
        if action == "inquiry" and not operator and method == "POST":
            return 200, self.inquire(h.user, oid, h.body()), {}
        if action == "reviews" and operator and method == "POST":
            return 200, self.review(h.user, oid, h.body()), {}
        if action == "actions" and operator and method == "POST":
            result = self.act(h.user, oid, h.body())
            return (202 if result.get("providerOutcome") == "unverified" else 200), result, {}
        raise ApiError(405, "이 환불 경로의 조회·저장 방식을 확인해주세요.")
