"""SQL-only interpretation of a separately verified current PG Payment.

This module cannot request a payment or a refund. It shares the original SQL
approval/cancellation effects and preserves unrelated or revoked access grants.
"""

from collections import namedtuple

from ..access import revoke, ensure_source_enrollment
from ..core import ApiError, new_id, now_iso
from .common import audit
from .order_products import authorize_product
from .refund_cancellation import apply_cancellation, observe_connection, _matching

Decision = namedtuple("Decision", "state code detail")


def matched(detail):
    return Decision("matched", "pg_match", detail)


def review(detail, code="unsupported_status"):
    return Decision("review", code, detail)


def retry(detail, code="unsupported_status"):
    return Decision("retry", code, detail)


class PaymentQueryProcessor:
    def __init__(self, app, commerce):
        self.app, self.commerce = app, commerce

    def _grants(self, c, order):
        rows = c.execute("SELECT g.*,e.user_id,e.course_id FROM enrollment_grants g JOIN enrollments e ON e.id=g.enrollment_id WHERE g.source='payment' AND g.source_id=?", (order["id"],)).fetchall()
        if any(r["user_id"] != order["user_id"] or r["course_id"] != order["course_id"] for r in rows):
            raise ApiError(409, "주문과 원 결제 권한의 연결을 조사해야 합니다.")
        return rows

    def _unpaid_terminal(self, c, order, payment):
        if order["status"] == "abandoned" and order["payment_key"] == payment["paymentKey"] and order["provider_status"] == payment["status"]:
            return matched("unpaid_terminal")
        c.execute("UPDATE purchase_orders SET status='abandoned',payment_key=?,provider_status=?,updated_at=?,version=version+1 WHERE id=?", (payment["paymentKey"], payment["status"], now_iso(), order["id"]))
        c.execute("INSERT INTO payment_events VALUES(?,?,?,?,?,?)", (new_id("event"), order["id"], "lookup", "verified", payment["status"], now_iso()))
        audit(c, None, "checkout.provider_terminal", "purchase", order["id"], {"status": payment["status"], "source": "provider_query"})
        return matched("unpaid_terminal")

    def __call__(self, c, order, payment):
        try:
            grants = self._grants(c, order)
        except ApiError:
            return review("missing_purchase_evidence")
        status, local = payment["status"], order["status"]
        balance = payment.get("balanceAmount")
        if status == "DONE":
            if type(balance) is not int or balance != order["amount"]:
                return review("provider_state_conflict", "amount_mismatch")
            if local == "refunding":
                return retry("refund_pending")  # Never downgrade a pending refund.
            if local == "refunded" or (local == "abandoned" and order["provider_status"] in ("CANCELED", "ABORTED", "EXPIRED")):
                return review("provider_state_conflict")
            if local == "paid":
                if not grants:
                    ensure_source_enrollment(c, order["user_id"], order["course_id"], source="payment", source_id=order["id"])
                    audit(c, None, "payment.access_reconciled", "purchase", order["id"], {"source": "provider_query"})
                    return matched("access_reconciled")
                # An explicit revocation/expiry is valid access intent, not an
                # invitation to revive that source from a repeated DONE notice.
                return matched("payment_current")
            if local not in ("created", "pending", "confirming", "abandoned"):
                return review("provider_state_conflict")
            started = bool(order["payment_key"]) or c.execute("SELECT 1 FROM audit_logs WHERE target_type='purchase' AND target_id=? AND action='checkout.authorization_started' LIMIT 1", (order["id"],)).fetchone()
            if not started or not c.execute("SELECT 1 FROM order_policy_sets WHERE order_id=?", (order["id"],)).fetchone():
                return review("missing_purchase_evidence")
            try:
                # Validate the frozen product/consent connection, not today's
                # price/deadline: the provider says the charge already happened.
                authorize_product(c, order)
            except ApiError:
                return review("missing_purchase_evidence")
            self.commerce.apply_verified_approval(c, None, order, payment["paymentKey"])
            return matched("payment_verified")
        if status == "CANCELED":
            if type(balance) is not int or balance != 0:
                return review("provider_state_conflict", "amount_mismatch")
            if local == "refunded":
                changed = False
                for grant in grants:
                    if grant["status"] != "revoked":
                        revoke(c, grant, "결제사 조회로 확인한 전액 취소", None)
                        changed = True
                if changed:
                    audit(c, None, "payment.access_reconciled", "purchase", order["id"], {"source": "provider_query", "status": "CANCELED"})
                return matched("cancellation_current")
            if local in ("paid", "refunding") or order["paid_at"]:
                operation = c.execute("SELECT * FROM refund_operations WHERE order_id=?", (order["id"],)).fetchone()
                if operation:
                    try:
                        _matching(operation, order)
                    except ApiError:
                        return review("provider_state_conflict")
                    if local != "refunding" or operation["status"] == "verified":
                        return review("provider_state_conflict")
                    if operation["status"] == "unverified":
                        # The v24 state machine requires an actual observer to
                        # enter checking before it can settle. This is the
                        # current leased GET, not another cancellation request.
                        c.execute("UPDATE refund_operations SET status='checking',version=version+1,updated_at=? WHERE id=?", (now_iso(), operation["id"]))
                        operation = c.execute("SELECT * FROM refund_operations WHERE id=?", (operation["id"],)).fetchone()
                    observe_connection(c, {"id": operation["initiated_by"]}, operation, True)
                    return matched("cancellation_verified")
                apply_cancellation(c, None, order, "결제사 조회에서 전액 취소가 확인되었습니다.")
                return matched("external_cancellation_verified")
            return self._unpaid_terminal(c, order, payment)
        if status == "PARTIAL_CANCELED":
            return review("partial_cancellation")
        if status in ("ABORTED", "EXPIRED"):
            if local in ("paid", "refunding", "refunded") or order["paid_at"]:
                return review("provider_state_conflict")
            return self._unpaid_terminal(c, order, payment)
        if local in ("paid", "refunding", "refunded"):
            # Includes virtual-account deposit reversals; no invented refund or
            # commercial access decision is made from a waiting provider state.
            return review("provider_state_conflict")
        if local == "abandoned":
            return matched("unpaid_closed")
        return retry("provider_pending")
