"""Transaction-bound refund facts, versions and own command receipts."""

from datetime import datetime, timedelta, timezone
import json
import re

from ..core import ApiError, new_id, now_iso
from .common import require_role
from .order_products import active_actor
from .policies import digest
from .refund_context import context_value

CHECKS = ("terms", "inquiry", "learning", "access")
EVENT_LIMIT = 30
LEASE_SECONDS = 30


def actor_order(c, user, order_id, operator=False):
    actor = active_actor(c, user)
    if operator:
        require_role(c, actor, "admin")
    order = c.execute("SELECT * FROM purchase_orders WHERE id=?" + ("" if operator else " AND user_id=?"), (order_id,) if operator else (order_id, actor["id"])).fetchone()
    if not order:
        raise ApiError(404, "조회할 수 있는 주문을 찾을 수 없습니다.")
    return actor, order


def case_version(c, oid):
    return c.execute("SELECT COALESCE(MAX(version),0) FROM refund_events WHERE order_id=?", (oid,)).fetchone()[0]


def expected_version(c, oid, expected):
    if type(expected) is not int or expected < 0:
        raise ApiError(400, "환불 처리 기록의 판본을 확인해주세요.")
    if case_version(c, oid) != expected:
        raise ApiError(409, "다른 처리 기록이 있습니다. 현재 기록을 다시 읽고 확인해주세요.")


def intent(data, kind, oid, fields):
    if not isinstance(data, dict) or set(data) != set(fields) | {"requestKey", "expectedVersion"}:
        raise ApiError(400, "환불 요청의 필수 항목과 요청 번호를 확인해주세요.")
    key = data["requestKey"]
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{12,120}", key):
        raise ApiError(400, "환불 요청 번호를 확인해주세요.")
    if type(data["expectedVersion"]) is not int or data["expectedVersion"] < 0:
        raise ApiError(400, "현재 처리 판본을 확인해주세요.")
    return {"key": key, "kind": kind, "hash": digest({"schemaVersion": 1, "orderId": oid, "kind": kind, "body": data})}


def command(c, actor, oid, key, request=None, operator=False):
    row = c.execute("SELECT * FROM refund_commands WHERE actor_id=? AND request_key=?", (actor["id"], key)).fetchone()
    if row and request and (row["order_id"] != oid or row["kind"] != request["kind"] or row["request_hash"] != request["hash"]):
        raise ApiError(409, "같은 요청 번호의 내용이 다릅니다. 원래 요청의 결과를 먼저 확인해주세요.")
    if row and (row["order_id"] != oid or (not operator and row["kind"] != "inquiry")):
        raise ApiError(404, "조회할 수 있는 요청 기록이 없습니다.")
    return json.loads(row["receipt"]) if row else None


def event(c, oid, kind, actor_id, body, review_id=None, operation_id=None):
    number = case_version(c, oid) + 1
    c.execute("INSERT INTO refund_events VALUES(?,?,?,?,?,?,?,?,?,?)", (new_id("refevent"), oid, number, kind, "operator" if kind == "review_saved" else "public", actor_id, review_id, operation_id, body, now_iso()))
    return number


def receipt(c, actor, oid, request, review_id=None, operation_id=None):
    value = {"schemaVersion": 1, "id": new_id("refcommand"), "requestKey": request["key"], "orderId": oid, "kind": request["kind"], "version": case_version(c, oid), "reviewId": review_id, "operationId": operation_id, "createdAt": now_iso()}
    c.execute("INSERT INTO refund_commands VALUES(?,?,?,?,?,?,?)", (actor["id"], request["key"], oid, request["kind"], request["hash"], json.dumps(value, ensure_ascii=False), value["createdAt"]))
    return value


def review_is_current(c, review, order):
    # The original viewer participates in the evidence fingerprint. Preserve that
    # identity for comparison; authorization always uses the current caller.
    return bool(review and order["status"] == "paid" and case_version(c, order["id"]) == review["expected_case_version"] + 1 and context_value(c, {"id": review["reviewer_id"]}, order, True)["contextHash"] == review["context_hash"])


def lease_until(row):
    return (datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")) + timedelta(seconds=LEASE_SECONDS)).isoformat(timespec="seconds").replace("+00:00", "Z")


def remaining_lease(row, at):
    if not row:
        return 0
    delta = datetime.fromisoformat(lease_until(row).replace("Z", "+00:00")) - datetime.fromisoformat(at.replace("Z", "+00:00"))
    return max(0, int(delta.total_seconds() + .999))


def case_view(c, actor, order, operator, payment, before=None):
    at = now_iso()
    context = context_value(c, actor, order, operator, at)
    operation = c.execute("SELECT * FROM refund_operations WHERE order_id=?", (order["id"],)).fetchone()
    legacy = c.execute("SELECT * FROM refund_legacy_attempts WHERE order_id=?", (order["id"],)).fetchone()
    latest = c.execute("SELECT * FROM refund_reviews WHERE order_id=? ORDER BY rowid DESC LIMIT 1", (order["id"],)).fetchone() if operator else None
    params = [order["id"]]
    where = "order_id=?" + ("" if operator else " AND visibility='public'")
    if before is not None:
        if type(before) is not int or before < 1:
            raise ApiError(400, "이력 페이지를 확인해주세요.")
        where += " AND version<?"; params.append(before)
    rows = c.execute("SELECT * FROM refund_events WHERE " + where + " ORDER BY version DESC LIMIT ?", (*params, EVENT_LIMIT + 1)).fetchall()
    events = [{"id": r["id"], "version": r["version"], "kind": r["kind"], "body": r["body"], "createdAt": r["created_at"], **({"reviewId": r["review_id"], "operationId": r["operation_id"], "actorId": r["actor_id"]} if operator else {})} for r in rows[:EVENT_LIMIT]]
    value = {"schemaVersion": 1, "viewerId": actor["id"], "audience": "operator" if operator else "member", "version": case_version(c, order["id"]), "context": context, "events": events,
             "page": {"limit": EVENT_LIMIT, "nextBefore": events[-1]["version"] if len(rows) > EVENT_LIMIT else None},
             "historyCoverage": "recorded" if c.execute("SELECT 1 FROM refund_events WHERE order_id=? AND kind='inquiry_submitted'", (order["id"],)).fetchone() else "legacy_state_only" if context["inquiry"] else "no_inquiry",
             "operation": ({"id": operation["id"], "origin": operation["origin"], "status": operation["status"], "createdAt": operation["created_at"], "updatedAt": operation["updated_at"], "recheckAt": lease_until(operation) if operation["status"] != "verified" else None} if operation else None)}
    if not operator:
        value["actions"] = {"canInquire": order["status"] == "paid" and context["inquiry"] is None}
        return value
    valid = review_is_current(c, latest, order) and operation is None
    value["currentReview"] = ({"id": latest["id"], "reviewerId": latest["reviewer_id"], "decision": latest["decision"], "basis": latest["basis"], "operatorNote": latest["operator_note"], "publicMessage": latest["public_message"], "cancelReason": latest["cancel_reason"], "checks": json.loads(latest["checks_json"]), "contextHash": latest["context_hash"], "orderVersion": latest["order_version"], "createdAt": latest["created_at"], "validForCurrentFacts": valid} if latest else None)
    connected = payment["configured"] and payment["mode"] == order["mode"]
    waiting = remaining_lease(operation or (order if legacy else None), at)
    value["gateway"] = {"configured": payment["configured"], "mode": payment["mode"], "matchesOrder": connected}
    value["legacyRecovery"] = bool(legacy and order["status"] == "refunding" and operation is None)
    value["actions"] = {"canReview": order["status"] == "paid" and operation is None,
                        "canExecute": bool(valid and (latest["decision"] == "reply" or connected)),
                        "canRecheck": bool(order["status"] == "refunding" and connected and not waiting and (operation or legacy)), "retryAfterSeconds": waiting}
    return value
