"""Typed course orders over the existing immutable price/condition records.

No product backfill: pre-descriptor orders retain their original classification.
Adding coaching/review/subscription sales requires its own provider and fulfillment.
"""

import hashlib
import json

from ..core import ApiError, text_field

PUBLIC_KINDS = ("terms", "privacy", "refund", "course_terms")
PRODUCT_FIELDS = frozenset(
    "schemaVersion type id version title quantity fulfillment billing provider mode amount currency".split()
)
DEFERRED_TYPES = frozenset(("coaching", "hand_review", "membership"))


def active_actor(c, user):
    actor = (
        c.execute(
            "SELECT * FROM users WHERE id=? AND status='active'", (user["id"],)
        ).fetchone()
        if user
        else None
    )
    if not actor:
        raise ApiError(401, "현재 계정으로 다시 로그인해주세요.")
    return actor


def course_reference(data):
    """Legacy courseId and explicit typed references resolve to the same factory."""
    if not isinstance(data, dict):
        raise ApiError(400, "주문 상품을 확인해주세요.")
    if "product" not in data:
        if "productType" in data and data["productType"] != "course":
            raise ApiError(400, "상품 유형은 product의 type으로 지정해주세요.")
        return text_field(data, "courseId", maximum=100)
    ref = data["product"]
    if not isinstance(ref, dict) or set(ref) != {"type", "id"}:
        raise ApiError(400, "상품 참조에는 type과 id만 지정해주세요.")
    kind = text_field(ref, "type", maximum=40)
    identifier = text_field(ref, "id", maximum=100)
    if kind in DEFERRED_TYPES:
        raise ApiError(409, "이 상품 유형의 결제·제공 조건은 아직 준비되지 않았습니다.")
    if kind != "course":
        raise ApiError(400, "지원하지 않는 상품 유형입니다.")
    if "productType" in data and data["productType"] != kind:
        raise ApiError(400, "상품 유형이 서로 일치하지 않습니다.")
    if "courseId" in data and text_field(data, "courseId", maximum=100) != identifier:
        raise ApiError(400, "상품 참조와 강의 식별자가 일치하지 않습니다.")
    return identifier


def course_product(course, price, mode):
    """Server-owned descriptor, also included in the existing consent digest."""
    return {
        "schemaVersion": 1,
        "type": "course",
        "id": course["id"],
        "version": course["version"],
        "title": course["title"],
        "quantity": 1,
        "fulfillment": "course_access",
        "billing": "one_time",
        "provider": "toss",
        "mode": mode,
        "amount": price["amount"],
        "currency": "KRW",
    }


def _invalid():
    raise ApiError(
        409,
        "주문 당시 상품·가격·동의 원본의 연결을 확인할 수 없습니다. 운영자에게 문의해주세요.",
    )


def _sealed(c, oid):
    record = c.execute(
        "SELECT * FROM order_policy_sets WHERE order_id=?", (oid,)
    ).fetchone()
    if not record:
        return None, None
    try:
        course = json.loads(record["course_snapshot"])
        if not isinstance(course, dict):
            _invalid()
        return record, course
    except (ValueError, TypeError):
        _invalid()


def product_summary(c, order):
    _, course = _sealed(c, order["id"])
    if not course or "product" not in course:
        return None
    product = course["product"]
    if not isinstance(product, dict) or set(product) != PRODUCT_FIELDS:
        _invalid()
    return product


def purchase_snapshot(c, order):
    """One read snapshot; only sealed public identities, no current price/terms."""
    record, course = _sealed(c, order["id"])
    product = product_summary(c, order)
    if product is None:
        return {
            "schemaVersion": 1,
            "recorded": False,
            "classification": "legacy_without_product_descriptor",
            "orderId": order["id"],
            "product": None,
        }
    if (
        type(product["schemaVersion"]) is not int
        or product["schemaVersion"] != 1
        or product["type"] != "course"
        or product["id"] != order["course_id"]
        or type(product["version"]) is not int
        or product["version"] != course.get("version")
        or product["title"] != order["title"]
        or type(product["quantity"]) is not int
        or product["quantity"] != 1
        or product["fulfillment"] != "course_access"
        or product["billing"] != "one_time"
        or product["provider"] != "toss"
        or product["mode"] != order["mode"]
        or product["mode"] not in ("test", "live")
        or type(product["amount"]) is not int
        or product["amount"] <= 0
        or product["amount"] != order["amount"]
        or product["currency"] != "KRW"
        or order["currency"] != "KRW"
        or course.get("id") != product["id"]
        or course.get("title") != product["title"]
        or course.get("amount") != product["amount"]
        or course.get("currency") != product["currency"]
    ):
        _invalid()
    price = c.execute(
        "SELECT * FROM order_price_snapshots WHERE order_id=?", (order["id"],)
    ).fetchone()
    if not price:
        _invalid()
    try:
        quote = json.loads(price["payload"])
        from .pricing import digest

        if (
            not isinstance(quote, dict)
            or digest(quote) != price["payload_hash"]
            or quote.get("courseId") != product["id"]
            or quote.get("courseVersion") != product["version"]
            or quote.get("title") != product["title"]
            or quote.get("amount") != product["amount"]
            or quote.get("currency") != product["currency"]
            or quote.get("mode") != product["mode"]
            or quote.get("id") != course.get("priceVersionId")
            or quote.get("hash") != course.get("priceHash")
            or quote.get("id") != price["price_id"]
            or quote.get("classification") != price["classification"]
        ):
            _invalid()
    except (ValueError, TypeError):
        _invalid()
    docs = c.execute(
        "SELECT d.*,p.edition FROM order_policy_documents d JOIN policy_versions p ON p.id=d.policy_version_id WHERE d.order_id=? ORDER BY d.kind,d.policy_version_id",
        (order["id"],),
    ).fetchall()
    public = [
        d for d in docs if d["kind"] in PUBLIC_KINDS and d["visibility"] == "public"
    ]
    if len(public) != len(PUBLIC_KINDS) or {d["kind"] for d in public} != set(
        PUBLIC_KINDS
    ):
        _invalid()
    accepted = c.execute(
        "SELECT policy_version_id,content_hash,accepted_at FROM consents WHERE order_id=? AND user_id=? AND context='purchase'",
        (order["id"], order["user_id"]),
    ).fetchall()
    if {(a["policy_version_id"], a["content_hash"]) for a in accepted} != {
        (d["policy_version_id"], d["content_hash"]) for d in public
    }:
        _invalid()
    times = {a["policy_version_id"]: a["accepted_at"] for a in accepted}
    result = {
        "schemaVersion": 1,
        "recorded": True,
        "classification": "typed_course",
        "orderId": order["id"],
        "product": product,
        "price": {
            "versionId": price["price_id"],
            "classification": price["classification"],
            "edition": quote.get("edition"),
            "priceHash": quote.get("hash"),
            "snapshotHash": price["payload_hash"],
        },
        "policy": {
            "fingerprint": record["fingerprint"],
            "sealedAt": record["sealed_at"],
            "paymentDeadline": record["payment_deadline"],
            "documents": [
                {
                    "id": d["policy_version_id"],
                    "kind": d["kind"],
                    "edition": d["edition"],
                    "contentHash": d["content_hash"],
                    "acceptedAt": times[d["policy_version_id"]],
                }
                for d in public
            ],
        },
    }
    canonical = json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return {**result, "snapshotHash": hashlib.sha256(canonical.encode()).hexdigest()}


def authorize_product(c, order):
    # Old orders keep the existing policy/price/legacy authorization paths.
    # New recorded descriptors must match all sealed sources before any PG call.
    if product_summary(c, order) is not None:
        purchase_snapshot(c, order)
