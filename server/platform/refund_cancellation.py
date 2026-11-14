"""One explicit cancellation job per order, with durable command replay fences."""

import json

from ..access import revoke
from ..core import ApiError, new_id, now_iso
from ..refund_records import key_hash
from .common import audit, notify
from .commerce import GatewayError
from .refund_case import actor_order, command, event, expected_version, intent, receipt, remaining_lease, review_is_current


def _gateway_ready(service, order):
    gateway = service.commerce.gateway
    if not gateway.ready:
        raise ApiError(503, "결제 상점이 연결되지 않았습니다. 검토 기록은 저장할 수 있습니다.")
    if order["mode"] != gateway.mode:
        raise ApiError(409, "주문을 생성한 결제 환경으로 설정한 뒤 확인해주세요.")
    if order["mode"] == "live" and not service.app.secure_cookies:
        raise ApiError(409, "실제 취소는 승인된 보안 운영 환경에서 처리해주세요.")
    if not order["payment_key"] or order["currency"] != "KRW" or type(order["amount"]) is not int:
        raise ApiError(409, "원래 승인된 결제 기록을 확인해주세요.")


def _matching(operation, order):
    if (operation["payment_key_hash"] != key_hash(order["payment_key"]) or operation["amount"] != order["amount"] or operation["currency"] != order["currency"] or operation["mode"] != order["mode"]):
        raise ApiError(409, "기존 취소 작업과 원래 결제 기록이 일치하지 않습니다. 운영 확인이 필요합니다.")


def _start(c, actor, order, review=None, legacy=None):
    at, opid = now_iso(), new_id("refop")
    reason = review["cancel_reason"] if review else legacy["original_reason"]
    public = review["public_message"] if review else reason
    c.execute("UPDATE purchase_orders SET status='refunding',refund_reason=?,updated_at=? WHERE id=?", (reason, at, order["id"]))
    c.execute("INSERT INTO refund_operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (opid, order["id"], review["id"] if review else None, "reviewed" if review else "legacy_recovery", actor["id"], key_hash(order["payment_key"]), order["amount"], order["currency"], order["mode"], reason, public, "checking", 1, at, at))
    event(c, order["id"], "cancellation_started" if review else "legacy_recovery_started", actor["id"], "같은 주문의 전액 취소 결과를 확인 중입니다. 아직 취소 완료가 아닙니다.", review_id=review["id"] if review else None, operation_id=opid)
    return c.execute("SELECT * FROM refund_operations WHERE id=?", (opid,)).fetchone()


def _reply(c, actor, order, review):
    inquiry = c.execute("SELECT * FROM refund_requests WHERE order_id=? AND status='requested'", (order["id"],)).fetchone()
    if not inquiry:
        raise ApiError(409, "답변할 수 있는 확인 대기 문의가 없습니다.")
    c.execute("UPDATE refund_requests SET status='rejected',resolution=?,updated_at=? WHERE id=?", (review["public_message"], now_iso(), inquiry["id"]))
    event(c, order["id"], "reply_sent", actor["id"], review["public_message"], review_id=review["id"])
    audit(c, actor, "refund.rejected", "purchase", order["id"], {"reviewId": review["id"], "reason": review["public_message"]})
    notify(c, order["user_id"], "취소 문의 답변이 등록되었습니다", review["public_message"], "#/purchases", event_key="refund.rejected:" + inquiry["id"], category="transaction", reference_kind="purchase", reference_id=order["id"], group_key="purchase:" + order["id"])


def _verified_result(result, order):
    return (isinstance(result, dict) and result.get("orderId") == order["id"] and result.get("paymentKey") == order["payment_key"] and result.get("currency") == "KRW" and type(result.get("totalAmount")) is int and result["totalAmount"] == order["amount"] and result.get("status") == "CANCELED" and type(result.get("balanceAmount")) is int and result["balanceAmount"] == 0)


def apply_cancellation(c, actor, order, message, operation=None, at=None):
    """SQL-only full-cancellation effect; no new refund decision or PG request."""
    at = at or now_iso()
    if operation is None:
        c.execute("INSERT INTO payment_events VALUES(?,?,?,?,?,?)", (new_id("event"), order["id"], "refund", "verified", "CANCELED", at))
    c.execute("UPDATE purchase_orders SET status='refunded',provider_status='CANCELED',updated_at=?,version=version+1 WHERE id=?", (at, order["id"]))
    for grant in c.execute("SELECT * FROM enrollment_grants WHERE source='payment' AND source_id=?", (order["id"],)).fetchall():
        revoke(c, grant, message, actor["id"] if actor else None)
    c.execute("UPDATE refund_requests SET status='approved',resolution=?,updated_at=? WHERE order_id=?", (message, at, order["id"]))
    if operation is not None:
        event(c, order["id"], "cancellation_verified", actor["id"], message, review_id=operation["review_id"], operation_id=operation["id"])
        details = {"reviewId": operation["review_id"], "operationId": operation["id"], "reason": operation["cancel_reason"], "amount": order["amount"]}
    else:
        details = {"source": "provider_query", "amount": order["amount"]}
    audit(c, actor, "payment.refunded", "purchase", order["id"], details)
    notify(c, order["user_id"], "전액 취소 처리가 완료되었습니다", message, "#/purchases", event_key="payment.refund:" + order["id"], category="transaction", reference_kind="purchase", reference_id=order["id"], group_key="purchase:" + order["id"])


def observe_connection(c, actor, operation, verified):
    current = c.execute("SELECT * FROM refund_operations WHERE id=?", (operation["id"],)).fetchone()
    order = c.execute("SELECT * FROM purchase_orders WHERE id=?", (current["order_id"],)).fetchone()
    if current["status"] == "verified":
        return
    _matching(current, order)
    if order["status"] != "refunding":
        raise ApiError(409, "현재 주문과 진행 중인 취소 작업을 함께 확인해야 합니다.")
    if not verified and current["version"] != operation["version"]:
        return  # A newer explicit observer owns the lease; never downgrade it.
    at = now_iso()
    c.execute("UPDATE refund_operations SET status=?,version=version+1,updated_at=? WHERE id=?", ("verified" if verified else "unverified", at, current["id"]))
    c.execute("INSERT INTO payment_events VALUES(?,?,?,?,?,?)", (new_id("event"), order["id"], "refund", "verified" if verified else "unverified", "CANCELED" if verified else None, at))
    if not verified:
        event(c, order["id"], "cancellation_unverified", actor["id"], "결제사 취소 결과를 아직 확인하지 못했습니다. 수강권은 검증 전까지 유지합니다.", review_id=current["review_id"], operation_id=current["id"])
        return
    # An already-authorized operation may settle after the initiator's role changed.
    apply_cancellation(c, actor, order, current["public_message"], current, at)


def _observe(service, actor, operation, verified):
    with service.app.db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        observe_connection(c, actor, operation, verified)


def perform(service, user, oid, data, expected_decision=None):
    operation = None
    with service.app.db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        actor, order = actor_order(c, user, oid, True)
        if not isinstance(data, dict) or data.get("action") not in ("execute", "recheck"):
            raise ApiError(400, "검토 기록과 명시 실행 또는 같은 주문 재확인 요청을 지정해주세요.")
        action = data["action"]
        request = intent(data, action, oid, {"action", "reviewId"} if action == "execute" else {"action"})
        review = None
        if action == "execute":
            if not isinstance(data["reviewId"], str):
                raise ApiError(400, "저장한 검토 기록을 확인해주세요.")
            review = c.execute("SELECT * FROM refund_reviews WHERE id=? AND order_id=?", (data["reviewId"], oid)).fetchone()
            if not review:
                raise ApiError(404, "조회할 수 있는 검토 기록이 없습니다.")
            if expected_decision and review["decision"] != expected_decision:
                raise ApiError(409, "이 실행 경로와 저장된 검토 방향이 다릅니다.")
        elif expected_decision == "reply":
            raise ApiError(409, "문의 답변 경로에서는 금융 결과를 재확인할 수 없습니다.")
        saved = command(c, actor, oid, request["key"], request, True)
        replayed = saved is not None
        if not saved:
            expected_version(c, oid, data["expectedVersion"])
            current = c.execute("SELECT * FROM refund_operations WHERE order_id=?", (oid,)).fetchone()
            if action == "execute":
                if current or not review_is_current(c, review, order):
                    raise ApiError(409, "검토 이후 기록이나 처리 상태가 바뀌었습니다. 현재 근거를 다시 확인해주세요.")
                if review["decision"] == "reply":
                    _reply(c, actor, order, review)
                else:
                    service.app.platform.reconciliation_runtime.require_scope(c, order)
                    _gateway_ready(service, order)
                    operation = _start(c, actor, order, review=review)
            else:
                service.app.platform.reconciliation_runtime.require_scope(c, order)
                _gateway_ready(service, order)
                if order["status"] != "refunding":
                    raise ApiError(409, "진행 중인 같은 주문의 취소만 재확인할 수 있습니다.")
                if current:
                    _matching(current, order)
                    if current["status"] == "verified" or remaining_lease(current, now_iso()):
                        raise ApiError(409, "취소 결과를 확인 중입니다. 표시된 재확인 시각 이후 다시 확인해주세요.")
                    c.execute("UPDATE refund_operations SET status='checking',version=version+1,updated_at=? WHERE id=?", (now_iso(), current["id"]))
                    c.execute("UPDATE purchase_orders SET updated_at=? WHERE id=?", (now_iso(), oid))
                    operation = c.execute("SELECT * FROM refund_operations WHERE id=?", (current["id"],)).fetchone()
                    event(c, oid, "cancellation_started", actor["id"], "원래 취소 사유와 같은 주문으로 결과를 재확인합니다.", review_id=current["review_id"], operation_id=current["id"])
                else:
                    legacy = c.execute("SELECT * FROM refund_legacy_attempts WHERE order_id=?", (oid,)).fetchone()
                    if not legacy or legacy["payment_key_hash"] != key_hash(order["payment_key"]) or legacy["amount"] != order["amount"] or legacy["currency"] != order["currency"] or legacy["mode"] != order["mode"] or legacy["original_reason"] != order["refund_reason"]:
                        raise ApiError(409, "기존 취소 시작 근거가 없습니다. 새 취소로 추정하지 않고 운영 확인이 필요합니다.")
                    if remaining_lease(order, now_iso()):
                        raise ApiError(409, "기존 취소 결과를 확인 중입니다. 잠시 후 같은 주문에서 확인해주세요.")
                    operation = _start(c, actor, order, legacy=legacy)
            saved = receipt(c, actor, oid, request, review_id=review["id"] if review else (operation["review_id"] if operation else None), operation_id=operation["id"] if operation else None)
            if operation:
                service.app.platform.reconciliation_runtime.schedule_cancellation(c, actor, order, operation, saved)
    if operation:
        try:
            result = service.commerce.gateway.cancel(order["payment_key"], oid, operation["cancel_reason"])
            if not _verified_result(result, order) or not service.app.platform.reconciliation_runtime.result_scope_matches(order, result):
                raise GatewayError("Unverified cancellation result")
        except GatewayError:
            _observe(service, actor, operation, False)
        else:
            _observe(service, actor, operation, True)
    result = service.result(user, oid, saved, True, replayed)
    outcome = result["refundCase"]["operation"]
    if outcome:
        result["providerOutcome"] = "verified" if outcome["status"] == "verified" else "unverified"
    return result
