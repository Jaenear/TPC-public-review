"""Single authorization start across devices, without treating the client as a PG.

A pending order without a payment key is an open authentication request. Closing
it only disables our future approval of that order; it is never a PG refund.
The existing order version and audit ledger suffice: no historical backfill.
"""

import sqlite3

from ..core import ApiError, now_iso, new_id
from ..access import for_member
from .common import audit
from .order_products import active_actor
from .checkout_preparations import find_preparation, request_key
from .checkout_preview import checkout_preview
from .checkout_result import result_hint


def other_payment(c, user_id, course_id, order_id=""):
    return c.execute(
        "SELECT id FROM purchase_orders WHERE user_id=? AND course_id=? AND id!=? "
        "AND status IN ('pending','confirming','refunding') LIMIT 1",
        (user_id, course_id, order_id),
    ).fetchone()


def initial_approval_guard(c, order):
    """Serialize legacy confirmations as well as the new SDK-start path."""
    if order["payment_key"]:
        return
    if other_payment(c, order["user_id"], order["course_id"], order["id"]):
        raise ApiError(
            409, "진행 중인 다른 주문이 있습니다. 내 주문에서 먼저 확인해주세요."
        )
    # A manual/pilot grant issued after preparation is an independent source;
    # it must not consume or invalidate this order's eventual payment grant.
    # Only another still-effective paid source represents duplicate purchase.
    access = for_member(c, order["user_id"], order["course_id"])
    if any(
        grant["source"] == "payment"
        and grant["reference"] != order["id"]
        and grant["status"] in ("active", "scheduled")
        for grant in access["grants"]
    ):
        raise ApiError(
            409, "다른 결제의 수강권이 이미 있습니다. 새 결제를 승인하지 않습니다."
        )


def state_view(order):
    return {
        "orderId": order["id"],
        "courseId": order["course_id"],
        "version": order["version"],
        "status": order["status"],
        "authorizationOpen": order["status"] == "pending" and not order["payment_key"],
        "canCloseAuthorization": order["status"] in ("created", "pending")
        and not order["payment_key"],
        "canReconcile": order["status"] in ("pending", "confirming"),
    }


def result(c, order, **extra):
    from .commerce import order_view

    return {
        "order": {k: v for k, v in order_view(c, order).items() if k != "paymentKey"},
        "checkoutState": state_view(order),
        "resultHint": result_hint(order),
        **extra,
    }


def body_version(data, fields):
    if not isinstance(data, dict) or set(data) != set(fields):
        raise ApiError(400, "현재 주문 판본과 요청 항목을 확인해주세요.")
    if type(data.get("version")) is not int or data["version"] < 1:
        raise ApiError(400, "현재 주문 판본을 확인해주세요.")
    return data["version"]


class CheckoutAuthorizationService:
    def __init__(self, commerce):
        self.commerce = commerce
        self.app = commerce.app

    def own(self, c, user, order_id):
        actor = active_actor(c, user)
        order = c.execute(
            "SELECT * FROM purchase_orders WHERE id=? AND user_id=?",
            (order_id, actor["id"]),
        ).fetchone()
        if not order:
            raise ApiError(404, "조회할 수 있는 주문을 찾을 수 없습니다.")
        return actor, order

    def state(self, user, order_id):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            _, order = self.own(c, user, order_id)
            return result(c, order)

    def begin(self, user, order_id, data):
        expected = body_version(data, ("version", "preparationKey"))
        key = request_key(data["preparationKey"])
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            actor, order = self.own(c, user, order_id)
            receipt = find_preparation(c, actor["id"], key)
            if not receipt or receipt["order_id"] != order_id:
                raise ApiError(404, "이 계정의 원래 주문 준비 기록을 찾을 수 없습니다.")
            # A replay never issues another permission to open the SDK. A lost
            # response is resolved in the server-state page, not by relaunching.
            if order["status"] != "created":
                return result(c, order, started=False)
            if order["version"] != expected:
                raise ApiError(
                    409, "주문 판본이 바뀌었습니다. 현재 상태를 다시 확인해주세요."
                )
            preview = checkout_preview(
                self.app, c, actor, order, self.commerce.config()
            )
            if not preview["canStart"]:
                raise ApiError(
                    409, " ".join(item["message"] for item in preview["blockers"])
                )
            initial_approval_guard(c, order)
            c.execute(
                "UPDATE purchase_orders SET status='pending',updated_at=?,version=version+1 WHERE id=?",
                (now_iso(), order_id),
            )
            authorization_event = audit(c, actor, "checkout.authorization_started", "purchase", order_id)
            self.app.platform.reconciliation_runtime.schedule_authorization(c, actor, c.execute("SELECT * FROM purchase_orders WHERE id=?", (order_id,)).fetchone(), "checkout", authorization_event)
            return result(
                c,
                c.execute(
                    "SELECT * FROM purchase_orders WHERE id=?", (order_id,)
                ).fetchone(),
                started=True,
            )

    def close(self, user, order_id, data):
        expected = body_version(data, ("version",))
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            actor, order = self.own(c, user, order_id)
            if order["status"] == "abandoned":
                return result(c, order, closed=True)
            if order["version"] != expected:
                raise ApiError(
                    409, "주문 상태가 바뀌었습니다. 현재 결과를 먼저 확인해주세요."
                )
            if not state_view(order)["canCloseAuthorization"]:
                raise ApiError(
                    409,
                    "승인 처리 중이거나 결제 식별자가 있는 주문은 여기서 종료할 수 없습니다. 같은 주문의 결제사 결과를 확인해주세요.",
                )
            c.execute(
                "UPDATE purchase_orders SET status='abandoned',updated_at=?,version=version+1 WHERE id=?",
                (now_iso(), order_id),
            )
            audit(c, actor, "checkout.authorization_closed", "purchase", order_id)
            return result(
                c,
                c.execute(
                    "SELECT * FROM purchase_orders WHERE id=?", (order_id,)
                ).fetchone(),
                closed=True,
            )

    def lookup_unapproved(self, user, order_id):
        """Explicit PG observation. Never infer absence/cancellation from a 404."""
        from .commerce import GatewayError

        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor, order = self.own(c, user, order_id)
            if order["status"] != "pending" or order["payment_key"]:
                raise ApiError(
                    409,
                    "인증 시작 기록이 없는 주문입니다. 현재 주문 상태를 다시 확인해주세요.",
                )
            gateway = self.commerce.gateway
            if not gateway.ready or gateway.mode != order["mode"]:
                raise ApiError(
                    503,
                    "주문을 시작한 결제 환경에서만 결제사 결과를 확인할 수 있습니다.",
                )
            lookup = getattr(gateway, "lookup_order", None)
            if not callable(lookup):
                raise ApiError(
                    503,
                    "현재 결제 연결은 주문 번호 조회를 지원하지 않습니다. 같은 주문 번호로 운영자에게 문의해주세요.",
                )
        self.app.limiter.check(("payment-lookup", actor["id"]), 30, 60)
        try:
            observed = lookup(order_id)
        except GatewayError as error:
            raise ApiError(502, str(error))
        key = observed.get("paymentKey") if isinstance(observed, dict) else None
        if (
            not isinstance(observed, dict)
            or observed.get("orderId") != order_id
            or not isinstance(key, str)
            or not 6 <= len(key) <= 200
            or type(observed.get("totalAmount")) is not int
            or observed["totalAmount"] != order["amount"]
            or observed.get("currency") != "KRW"
            or observed.get("status")
            not in (
                "READY",
                "IN_PROGRESS",
                "WAITING_FOR_DEPOSIT",
                "DONE",
                "CANCELED",
                "PARTIAL_CANCELED",
                "ABORTED",
                "EXPIRED",
            )
        ):
            raise ApiError(
                502,
                "결제사 조회 결과가 원래 주문과 일치하지 않습니다. 새 결제를 시작하지 말고 같은 주문을 확인해주세요.",
            )
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            actor, current = self.own(c, actor, order_id)
            if (
                not gateway.ready
                or gateway.mode != order["mode"]
                or self.commerce.gateway is not gateway
            ):
                raise ApiError(
                    409,
                    "조회 중 결제 환경이 바뀌었습니다. 현재 주문을 다시 확인해주세요.",
                )
            if (
                current["status"] != "pending"
                or current["payment_key"]
                or current["version"] != order["version"]
            ):
                raise ApiError(
                    409,
                    "조회 중 주문 상태가 바뀌었습니다. 현재 주문을 다시 확인해주세요.",
                )
            status = observed["status"]
            if status in ("CANCELED", "ABORTED", "EXPIRED"):
                if status == "CANCELED" and (
                    type(observed.get("balanceAmount")) is not int
                    or observed["balanceAmount"] != 0
                ):
                    raise ApiError(502, "결제사 취소 잔액을 확인할 수 없습니다.")
                try:
                    c.execute(
                        "UPDATE purchase_orders SET status='abandoned',payment_key=?,provider_status=?,updated_at=?,version=version+1 WHERE id=?",
                        (key, status, now_iso(), order_id),
                    )
                except sqlite3.IntegrityError:
                    raise ApiError(409, "다른 주문에 연결된 결제 식별자입니다.")
                c.execute(
                    "INSERT INTO payment_events VALUES(?,?,?,?,?,?)",
                    (
                        new_id("event"),
                        order_id,
                        "lookup",
                        "verified",
                        status,
                        now_iso(),
                    ),
                )
                audit(
                    c,
                    actor,
                    "checkout.provider_terminal",
                    "purchase",
                    order_id,
                    {"status": status},
                )
                return result(
                    c,
                    c.execute(
                        "SELECT * FROM purchase_orders WHERE id=?", (order_id,)
                    ).fetchone(),
                    providerObservation={"status": status, "at": now_iso()},
                )
            if status not in ("IN_PROGRESS", "DONE"):
                return result(
                    c, current, providerObservation={"status": status, "at": now_iso()}
                )
        # The ordinary approval boundary rechecks ownership, current conditions,
        # competing orders, key uniqueness and every PG field. A concurrent close
        # wins before approval starts, or cannot close after the key is durable.
        return self.commerce.confirm(
            actor, {"orderId": order_id, "paymentKey": key, "amount": order["amount"]}
        )
