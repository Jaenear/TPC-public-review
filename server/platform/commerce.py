"""Toss Payments verification. No local 'successful payment' fallback exists."""

import base64
import http.client
from datetime import datetime, timezone
import json
from .contracts import seal_order_contract, authorize_contract
from .pricing import seal_price, authorize_price
from .order_products import (
    active_actor,
    course_reference,
    product_summary,
    purchase_snapshot,
    authorize_product,
)
from .checkout_preparations import (
    preparation_intent,
    request_key,
    find_preparation,
    record_preparation,
    preparation_view,
)
from .checkout_preview import checkout_preview
from .checkout_authorization import (
    CheckoutAuthorizationService,
    initial_approval_guard,
)
from .reconciliation_ingress import security_keys
from .reconciliation_contract import opaque
import os
import socket
import sqlite3
import urllib.error
import urllib.request
from urllib.parse import quote
from ..core import ApiError, text_field, now_iso, new_id
from .common import require_role, row_dict, audit, notify, has_access
from ..access import revoke, ensure_source_enrollment
from .policies import (
    authorize_payment,
    conditions_bundle,
    explicit_selection,
    order_policy_summary,
    seal_order,
)


def order_view(c, row):
    return {
        **row_dict(row),
        "policy": order_policy_summary(c, row["id"]),
        "product": product_summary(c, row),
    }


class GatewayError(Exception):
    def __init__(self, message, kind="unavailable"):
        super().__init__(message)
        self.kind = kind


class TossGateway:
    def __init__(self):
        self.client_key = os.environ.get("TPC_TOSS_CLIENT_KEY", "")
        self.secret_key = os.environ.get("TPC_TOSS_SECRET_KEY", "")
        self.mode = (
            "test"
            if self.client_key.startswith("test_")
            and self.secret_key.startswith("test_")
            else (
                "live"
                if self.client_key.startswith("live_")
                and self.secret_key.startswith("live_")
                and os.environ.get("TPC_ALLOW_LIVE_PAYMENTS") == "1"
                else "unconfigured"
            )
        )
        self.ready = self.mode != "unconfigured"
        self.merchant_id = os.environ.get("TPC_TOSS_MERCHANT_ID", "")
        self.webhook_security_keys = security_keys(os.environ.get("TPC_TOSS_WEBHOOK_SECURITY_KEYS", ""))
        self.lookup_secret_key = os.environ.get("TPC_TOSS_LOOKUP_SECRET_KEY", "")

    def request(self, method, path, data=None, key=None, *, lookup_secret=None):
        if not self.ready:
            raise GatewayError("결제 상점 키가 설정되지 않았습니다.")
        if lookup_secret is not None and (method != "GET" or not lookup_secret.startswith(self.mode + "_sk")):
            raise GatewayError("같은 환경의 조회 전용 연결을 확인해주세요.")
        headers = {
            "Authorization": "Basic "
            + base64.b64encode(((lookup_secret or self.secret_key) + ":").encode()).decode(),
            "Content-Type": "application/json",
        }
        if key:
            headers["Idempotency-Key"] = key
        request = urllib.request.Request(
            "https://api.tosspayments.com/v1" + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                result = json.load(response)
                if not isinstance(result, dict):
                    raise GatewayError("결제사 응답 형식을 확인하지 못했습니다.")
                return result
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, http.client.HTTPException) as error:
            raise GatewayError(
                "결제사 처리 결과를 확인하지 못했습니다. 같은 주문에서 다시 확인해주세요.",
                "timeout" if isinstance(error, (TimeoutError, socket.timeout)) or isinstance(getattr(error, "reason", None), (TimeoutError, socket.timeout)) else "unavailable",
            ) from error

    def confirm(self, payment_key, order_id, amount):
        try:
            return self.request(
                "POST",
                "/payments/confirm",
                {"paymentKey": payment_key, "orderId": order_id, "amount": amount},
                "confirm-" + order_id,
            )
        except GatewayError:
            # A timeout may happen after provider approval. Reconcile by key;
            # the caller still verifies order, amount, currency and state.
            return self.request("GET", "/payments/" + quote(payment_key, safe=""))

    def cancel(self, payment_key, order_id, reason):
        try:
            return self.request(
                "POST",
                "/payments/" + quote(payment_key, safe="") + "/cancel",
                {"cancelReason": reason},
                "refund-" + order_id,
            )
        except GatewayError:
            return self.request("GET", "/payments/" + quote(payment_key, safe=""))

    def lookup_order(self, order_id):
        # Toss's order lookup requires an API individual-integration secret.
        # Widget-only gsk keys must not silently fall back to a successful result.
        lookup_secret = None
        if not self.secret_key.startswith(("test_sk", "live_sk")):
            if self.lookup_secret_key.startswith(self.mode + "_sk"):
                lookup_secret = self.lookup_secret_key
            else:
                raise GatewayError(
                    "현재 결제 연결에서는 주문 번호로 결과를 확인할 수 없습니다. 결제 결과 화면으로 복귀하거나 운영자에게 같은 주문 번호를 알려주세요."
                )
        if lookup_secret is not None:
            return self.request("GET", "/payments/orders/" + quote(order_id, safe=""), lookup_secret=lookup_secret)
        return self.request("GET", "/payments/orders/" + quote(order_id, safe=""))

    def lookup_payment(self, payment_key):
        return self.request("GET", "/payments/" + quote(payment_key, safe=""))

    def can_lookup_order(self):
        return self.ready and (self.secret_key.startswith(self.mode + "_sk") or self.lookup_secret_key.startswith(self.mode + "_sk"))


class CommerceService:
    def __init__(self, app, gateway=None):
        self.app = app
        self.gateway = gateway or TossGateway()
        self.authorization = CheckoutAuthorizationService(self)

    def config(self):
        return {
            "provider": "toss",
            "mode": self.gateway.mode,
            "configured": self.gateway.ready,
            "clientKey": self.gateway.client_key if self.gateway.ready else None,
            "productTypes": {
                "course": "one_time",
                "coaching": "unavailable",
                "hand_review": "unavailable",
                "membership": "later",
            },
        }

    def create(self, user, data):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        course_id = course_reference(data)
        intent = preparation_intent(data, course_id)
        if intent:
            with self.app.db.connect() as c:
                c.execute("BEGIN")
                actor = active_actor(c, user)
                receipt = find_preparation(c, actor["id"], intent["key"], intent)
                if receipt:
                    return self.prepared_result(c, receipt, replayed=True)
        # The limiter owns another writer; do not invoke it under our writer lock.
        self.app.limiter.check(("checkout", user["id"]), 20, 3600)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = active_actor(c, user)
            if intent:
                receipt = find_preparation(c, user["id"], intent["key"], intent)
                if receipt:
                    return self.prepared_result(c, receipt, replayed=True)
            course = c.execute(
                "SELECT * FROM courses WHERE id=? AND status='published'", (course_id,)
            ).fetchone()
            if not course:
                raise ApiError(404, "현재 판매 중인 강의를 찾을 수 없습니다.")
            if course["pilot"] and not course["price"]:
                raise ApiError(400, "무료 강의는 바로 수강 등록할 수 있습니다.")
            if has_access(c, user, course_id):
                raise ApiError(409, "이미 수강권이 있는 강의입니다.")
            if not self.gateway.ready:
                raise ApiError(
                    503,
                    "토스페이먼츠 상점 연동을 준비 중입니다. 현재 결제는 발생하지 않습니다.",
                )
            if self.gateway.mode == "live" and (
                course["pilot"] or not self.app.secure_cookies
            ):
                raise ApiError(
                    409,
                    "파일럿 강의 또는 로컬 환경에서는 실제 결제를 받을 수 없습니다.",
                )
            unresolved = c.execute(
                "SELECT 1 FROM purchase_orders WHERE user_id=? AND course_id=? AND status IN ('pending','confirming','refunding')",
                (user["id"], course_id),
            ).fetchone()
            if unresolved:
                raise ApiError(
                    409,
                    "승인 결과를 확인해야 하는 이전 주문이 있습니다. 내 주문에서 같은 주문의 승인 결과를 먼저 확인해주세요.",
                )
            bundle = conditions_bundle(c, "checkout", self.gateway.mode, course_id)
            explicit_selection(bundle, data)
            amount = bundle["price"]["amount"]
            if amount == 0:
                raise ApiError(400, "무료 강의는 바로 수강 등록할 수 있습니다.")
            old = c.execute(
                "SELECT * FROM purchase_orders WHERE user_id=? AND course_id=? AND mode=? AND status IN ('created','pending') ORDER BY created_at DESC LIMIT 1",
                (user["id"], course_id, self.gateway.mode),
            ).fetchone()
            previous = order_policy_summary(c, old["id"]) if old else None
            if (
                old
                and old["amount"] == amount
                and product_summary(c, old) == bundle["course"]["product"]
                and previous["fingerprint"] == bundle["fingerprint"]
                and previous["paymentDeadline"] > bundle["asOf"]
            ):
                self.app.platform.reconciliation_runtime.bind_checkout(c, user, old["id"])
                receipt = record_preparation(c, user["id"], old["id"], intent)
                return (
                    self.prepared_result(c, receipt)
                    if receipt
                    else {"order": order_view(c, old), "payment": self.config()}
                )
            order_id = new_id("tpcorder")
            timestamp = now_iso()
            c.execute(
                "INSERT INTO purchase_orders(id,user_id,course_id,title,amount,mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    order_id,
                    user["id"],
                    course_id,
                    course["title"],
                    amount,
                    self.gateway.mode,
                    timestamp,
                    timestamp,
                ),
            )
            seal_order(c, order_id, user["id"], bundle)
            seal_order_contract(c, order_id, bundle["asOf"])
            seal_price(c, order_id, bundle["price"], bundle["asOf"])
            self.app.platform.reconciliation_runtime.bind_checkout(c, user, order_id)
            audit(
                c,
                user,
                "order.created",
                "purchase",
                order_id,
                {
                    "amount": amount,
                    "mode": self.gateway.mode,
                    "policyDigest": bundle["fingerprint"],
                },
            )
            receipt = record_preparation(c, user["id"], order_id, intent)
            if receipt:
                return self.prepared_result(c, receipt)
            return {
                "order": order_view(
                    c,
                    c.execute(
                        "SELECT * FROM purchase_orders WHERE id=?", (order_id,)
                    ).fetchone(),
                ),
                "payment": self.config(),
            }

    def prepared_result(self, c, receipt, replayed=False):
        order = c.execute(
            "SELECT * FROM purchase_orders WHERE id=? AND user_id=?",
            (receipt["order_id"], receipt["user_id"]),
        ).fetchone()
        if not order:
            raise ApiError(409, "원래 주문과 준비 요청의 연결을 확인할 수 없습니다.")
        return {
            "order": {
                k: v for k, v in order_view(c, order).items() if k != "paymentKey"
            },
            "payment": self.config(),
            "preparation": preparation_view(receipt),
            "replayed": replayed,
        }

    def preparation(self, user, key):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor = active_actor(c, user)
            receipt = find_preparation(c, actor["id"], request_key(key))
            if not receipt:
                raise ApiError(
                    404,
                    "아직 확인된 주문 준비 기록이 없습니다. 같은 요청으로 다시 확인할 수 있습니다.",
                )
            return self.prepared_result(c, receipt, replayed=True)

    def preview(self, user, order_id):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor = active_actor(c, user)
            order = c.execute(
                "SELECT * FROM purchase_orders WHERE id=? AND user_id=?",
                (order_id, actor["id"]),
            ).fetchone()
            if not order:
                raise ApiError(404, "조회할 수 있는 주문을 찾을 수 없습니다.")
            return {
                "checkoutPreview": checkout_preview(
                    self.app, c, actor, order, self.config()
                )
            }

    def orders(self, user, admin=False):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user = active_actor(c, user)
            if admin:
                require_role(c, user, "admin")
            rows = c.execute(
                "SELECT o.*,u.name AS buyer,u.email AS buyerEmail FROM purchase_orders o JOIN users u ON u.id=o.user_id "
                + ("" if admin else "WHERE o.user_id=? ")
                + "ORDER BY o.created_at DESC LIMIT 200",
                () if admin else (user["id"],),
            ).fetchall()
            result = []
            for row in rows:
                item = {
                    key: value
                    for key, value in order_view(c, row).items()
                    if key != "paymentKey"
                }
                request = c.execute(
                    "SELECT * FROM refund_requests WHERE order_id=?", (row["id"],)
                ).fetchone()
                item["refundRequest"] = row_dict(request) if request else None
                result.append(item)
            return {"orders": result}

    def snapshot(self, user, order_id, admin=False):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor = active_actor(c, user)
            if admin:
                require_role(c, actor, "admin")
            row = c.execute(
                "SELECT * FROM purchase_orders WHERE id=?"
                + ("" if admin else " AND user_id=?"),
                (order_id,) if admin else (order_id, actor["id"]),
            ).fetchone()
            if not row:
                raise ApiError(404, "조회할 수 있는 주문을 찾을 수 없습니다.")
            return {"purchaseSnapshot": purchase_snapshot(c, row)}

    def confirm(self, user, data):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        if not isinstance(data, dict) or set(data) != {
            "orderId",
            "paymentKey",
            "amount",
        }:
            raise ApiError(
                400, "승인 요청의 주문 번호·결제 식별자·금액을 확인해주세요."
            )
        self.app.limiter.check(("payment-confirm", user["id"]), 30, 60)
        order_id = text_field(data, "orderId", minimum=6, maximum=64)
        try:
            payment_key = opaque(data["paymentKey"], 200, 6)
        except ApiError:
            raise ApiError(400, "결제 식별자의 자료형과 길이를 확인해주세요.") from None
        amount = data.get("amount")
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = active_actor(c, user)
            order = c.execute(
                "SELECT * FROM purchase_orders WHERE id=? AND user_id=?",
                (order_id, user["id"]),
            ).fetchone()
            if not order:
                raise ApiError(404, "주문을 찾을 수 없습니다.")
            if type(amount) is not int or amount != order["amount"]:
                raise ApiError(400, "주문 금액이 일치하지 않습니다.")
            if order["payment_key"] and order["payment_key"] != payment_key:
                raise ApiError(409, "이미 다른 결제 식별자로 처리된 주문입니다.")
            if order["status"] == "paid":
                return {"order": order_view(c, order)}
            if order["status"] not in ("created", "pending", "confirming"):
                raise ApiError(409, "승인할 수 없는 주문 상태입니다.")
            if (
                order["status"] == "confirming"
                and (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(order["updated_at"].replace("Z", "+00:00"))
                ).total_seconds()
                < 30
            ):
                raise ApiError(
                    409, "승인 결과를 확인하고 있습니다. 잠시 후 다시 확인해주세요."
                )
            if not self.gateway.ready:
                raise ApiError(503, "결제 상점이 연결되지 않았습니다.")
            if order["mode"] != self.gateway.mode:
                raise ApiError(
                    409,
                    "주문을 생성한 결제 환경과 현재 설정이 다릅니다. 운영자에게 확인해주세요.",
                )
            initial_approval_guard(c, order)
            authorize_product(c, order)
            authorize_price(c, order)
            authorize_contract(c, order)
            authorize_payment(c, order)
            self.app.platform.reconciliation_runtime.require_scope(c, order)
            self.app.platform.reconciliation_runtime.schedule_authorization(c, user, order)
            try:
                c.execute(
                    "UPDATE purchase_orders SET status='confirming',payment_key=?,updated_at=? WHERE id=?",
                    (payment_key, now_iso(), order_id),
                )
            except sqlite3.IntegrityError:
                raise ApiError(409, "이미 다른 주문에 사용된 결제입니다.")
        try:
            result = self.gateway.confirm(payment_key, order_id, amount)
            if (
                not isinstance(result, dict)
                or result.get("paymentKey") != payment_key
                or result.get("orderId") != order_id
                or type(result.get("totalAmount")) is not int
                or result["totalAmount"] != amount
                or result.get("currency") != "KRW"
                or result.get("status") != "DONE"
                or not self.app.platform.reconciliation_runtime.result_scope_matches(order, result)
            ):
                raise GatewayError("결제사 승인 정보가 주문과 일치하지 않습니다.")
        except GatewayError as error:
            with self.app.db.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                settled = c.execute("SELECT * FROM purchase_orders WHERE id=?", (order_id,)).fetchone()
                if settled["status"] == "paid" and settled["payment_key"] == payment_key:
                    return {"order": order_view(c, settled)}
                c.execute(
                    "UPDATE purchase_orders SET status='pending',updated_at=? WHERE id=? AND status='confirming'",
                    (now_iso(), order_id),
                )
                c.execute(
                    "INSERT INTO payment_events VALUES(?,?,?,?,?,?)",
                    (
                        new_id("event"),
                        order_id,
                        "confirm",
                        "unverified",
                        None,
                        now_iso(),
                    ),
                )
            raise ApiError(502, str(error))
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            current = c.execute(
                "SELECT status FROM purchase_orders WHERE id=?", (order_id,)
            ).fetchone()[0]
            self.app.platform.reconciliation_runtime.require_scope(c, order)
            if current == "paid":
                return {"order": order_view(c, c.execute("SELECT * FROM purchase_orders WHERE id=?", (order_id,)).fetchone())}
            if current not in ("confirming", "pending", "paid"):
                raise ApiError(
                    409, "주문 상태가 변경되었습니다. 운영자에게 확인해주세요."
                )
            return self.apply_verified_approval(c, user, order)

    def apply_verified_approval(self, c, actor, order, observed_key=None):
        """SQL-only effect shared by ordinary confirmation and fresh PG GET.

        Callers must validate the financial result and current transition first.
        A recovery is a system observation (actor=None), never an invented login.
        """
        order_id = order["id"]
        if observed_key is not None:
            c.execute("UPDATE purchase_orders SET payment_key=? WHERE id=?", (observed_key, order_id))
        ensure_source_enrollment(c, order["user_id"], order["course_id"], source="payment", source_id=order_id)
        c.execute(
            "UPDATE purchase_orders SET status='paid',provider_status='DONE',paid_at=COALESCE(paid_at,?),updated_at=?,version=version+1 WHERE id=?",
            (now_iso(), now_iso(), order_id),
        )
        c.execute("INSERT INTO payment_events VALUES(?,?,?,?,?,?)", (new_id("event"), order_id, "confirm", "verified", "DONE", now_iso()))
        audit(c, actor, "payment.verified", "purchase", order_id, {"amount": order["amount"]})
        notify(c, order["user_id"], "수강권이 열렸습니다", order["title"], "#/my",
               event_key="payment.confirm:" + order_id, category="transaction", reference_kind="purchase", reference_id=order_id, group_key="purchase:" + order_id)
        return {"order": order_view(c, c.execute("SELECT * FROM purchase_orders WHERE id=?", (order_id,)).fetchone())}

    def reconcile(self, user, order_id):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user = active_actor(c, user)
            order = c.execute(
                "SELECT * FROM purchase_orders WHERE id=? AND user_id=?",
                (order_id, user["id"]),
            ).fetchone()
            if not order:
                raise ApiError(404, "주문을 찾을 수 없습니다.")
            if not order["payment_key"]:
                unknown = True
            else:
                unknown = False
        if unknown:
            return self.authorization.lookup_unapproved(user, order_id)
        return self.confirm(
            user,
            {
                "orderId": order_id,
                "paymentKey": order["payment_key"],
                "amount": order["amount"],
            },
        )

    def refund(self, user, order_id, data):
        result = self.app.platform.refund_workflow.act(user, order_id, data, "full_cancel")
        if result.get("providerOutcome") == "unverified":
            raise ApiError(502, "취소 결과를 아직 확인하지 못했습니다. 같은 주문의 기록을 읽고 명시적으로 재확인해주세요.")
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor = active_actor(c, user)
            require_role(c, actor, "admin")
            row = c.execute("SELECT * FROM purchase_orders WHERE id=?", (order_id,)).fetchone()
            return {"order": {key: value for key, value in order_view(c, row).items() if key != "paymentKey"}, "receipt": result["receipt"], "replayed": result["replayed"]}

    def refund_request(self, user, order_id, data):
        result = self.app.platform.refund_workflow.inquire(user, order_id, data)
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor = active_actor(c, user)
            row = c.execute("SELECT * FROM refund_requests WHERE order_id=? AND user_id=?", (order_id, actor["id"])).fetchone()
            return {"request": row_dict(row), "receipt": result["receipt"], "replayed": result["replayed"]}

    def reject_refund(self, user, order_id, data):
        result = self.app.platform.refund_workflow.act(user, order_id, data, "reply")
        return {"updated": True, "receipt": result["receipt"], "replayed": result["replayed"]}
