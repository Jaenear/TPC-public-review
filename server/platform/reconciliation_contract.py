"""Small, secret-free contracts for internal reconciliation callers.

Scope must come from server configuration, never from a webhook's merchant data.
Event hints are untrusted; only a separate PG query may enter the match boundary.
"""

from dataclasses import dataclass
import hashlib
import json
import re

from ..core import ApiError

EVENT_TYPES = frozenset(("PAYMENT_STATUS_CHANGED", "DEPOSIT_CALLBACK", "CANCEL_STATUS_CHANGED"))
PG_STATUSES = frozenset(("READY", "IN_PROGRESS", "WAITING_FOR_DEPOSIT", "DONE", "CANCELED", "PARTIAL_CANCELED", "ABORTED", "EXPIRED"))


def opaque(value, maximum=256, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ApiError(400, "대사 참조 값의 길이와 자료형을 확인해주세요.")
    return value  # Provider identifiers are opaque: no trimming/case folding/regex.


def hashed(value):
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def document(value):
    """Bounded JSON, hashed before discarding all raw/customer/secret fields."""
    remaining = [4096]

    def check(node, depth=0):
        remaining[0] -= 1
        if depth > 12 or remaining[0] < 0:
            raise ValueError()
        if isinstance(node, dict):
            if not all(isinstance(k, str) for k in node):
                raise ValueError()
            for child in node.values():
                check(child, depth + 1)
        elif isinstance(node, list):
            for child in node:
                check(child, depth + 1)
        elif node is not None and type(node) not in (str, int, float, bool):
            raise ValueError()

    try:
        if not isinstance(value, dict):
            raise ValueError()
        check(value)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        if len(encoded) > 65536:
            raise ValueError()
        return encoded
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ApiError(400, "이벤트 JSON의 형식과 크기를 확인해주세요.") from None


@dataclass(frozen=True)
class Scope:
    provider: str
    mode: str
    merchant_hash: str

    def __post_init__(self):
        if self.provider != "toss" or self.mode not in ("test", "live") or not isinstance(self.merchant_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", self.merchant_hash):
            raise ApiError(400, "서버의 결제 상점·환경 범위를 확인해주세요.")

    @classmethod
    def toss(cls, mode, merchant_reference):
        return cls("toss", mode, hashed(opaque(merchant_reference, 200)))

    @property
    def values(self):
        return (self.provider, self.mode, self.merchant_hash)


@dataclass(frozen=True)
class TechnicalPolicy:
    """Frozen technical defaults, not approval of DEC-11 operational policy."""
    max_attempts: int = 5
    base_delay: int = 15
    max_delay: int = 900
    lease_seconds: int = 30

    def __post_init__(self):
        if (any(type(x) is not int for x in self.values) or not 1 <= self.max_attempts <= 10 or
                not 1 <= self.base_delay <= 3600 or not self.base_delay <= self.max_delay <= 86400 or not 5 <= self.lease_seconds <= 300):
            raise ApiError(400, "대사 기술 재시도 설정을 확인해주세요.")

    @property
    def values(self):
        return (self.max_attempts, self.base_delay, self.max_delay, self.lease_seconds)


@dataclass(frozen=True)
class Claim:
    job_id: str
    attempt_id: str
    token: str


def order_facts(order):
    return {"id": order["id"], "version": order["version"], "status": order["status"],
            "amount": order["amount"], "currency": order["currency"], "mode": order["mode"],
            "paymentKeyHash": hashed(order["payment_key"]) if order["payment_key"] else None}


def checked_version(order, expected):
    if type(expected) is not int or expected < 1:
        raise ApiError(400, "주문 판본을 확인해주세요.")
    if order["version"] != expected:
        raise ApiError(409, "주문이 변경되었습니다. 최신 주문을 확인해주세요.")


def query_match(scope, order, payment):
    """Validate only separately queried Payment data. No webhook is a proof.

    Missing merchant/key/status or wrong references fail closed. The .02 worker
    must obtain this object through the configured same-scope PG lookup adapter.
    """
    if not isinstance(payment, dict):
        return "reference_mismatch"
    mid = payment.get("mId")
    if not isinstance(mid, str) or not mid or hashed(mid) != scope.merchant_hash or order["mode"] != scope.mode:
        return "scope_mismatch"
    if payment.get("orderId") != order["id"]:
        return "reference_mismatch"
    if type(payment.get("totalAmount")) is not int or payment["totalAmount"] != order["amount"]:
        return "amount_mismatch"
    if payment.get("currency") != order["currency"] or order["currency"] != "KRW":
        return "currency_mismatch"
    key = payment.get("paymentKey")
    if not isinstance(key, str) or not 1 <= len(key) <= 1000 or (order["payment_key"] and hashed(key) != hashed(order["payment_key"])):
        return "payment_key_mismatch"
    if not isinstance(payment.get("status"), str) or payment["status"] not in PG_STATUSES:
        return "unsupported_status"
    return "pg_match"
