"""Immutable own preparation receipts. A read never starts or approves payment."""

import hashlib
import json
import re

from ..core import ApiError, now_iso

PREPARATION_TABLES = {"checkout_preparations"}


def request_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{12,120}", value):
        raise ApiError(400, "주문 준비 요청 번호를 확인해주세요.")
    return value


def preparation_intent(data, course_id):
    if "requestKey" not in data:
        return None  # Preserve the existing non-idempotent request contract.
    key = request_key(data["requestKey"])
    allowed = {"requestKey", "courseId", "product", "policyDigest", "acceptedPolicyIds"}
    accepted = data.get("acceptedPolicyIds")
    digest = data.get("policyDigest")
    if (
        set(data) - allowed
        or not isinstance(digest, str)
        or not re.fullmatch(r"[a-f0-9]{64}", digest)
        or not isinstance(accepted, list)
        or len(accepted) != 4
        or any(not isinstance(p, str) or not 1 <= len(p) <= 100 for p in accepted)
        or len(set(accepted)) != 4
    ):
        raise ApiError(400, "주문 준비 요청의 상품·동의 판본을 확인해주세요.")
    payload = {
        "schemaVersion": 1,
        "courseId": course_id,
        "policyDigest": digest,
        "acceptedPolicyIds": sorted(accepted),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return {
        "key": key,
        "hash": hashlib.sha256(encoded.encode()).hexdigest(),
        "json": encoded,
    }


def find_preparation(c, user_id, key, intent=None):
    row = c.execute(
        "SELECT * FROM checkout_preparations WHERE user_id=? AND request_key=?",
        (user_id, key),
    ).fetchone()
    if (
        row
        and intent
        and (
            row["request_hash"] != intent["hash"]
            or row["request_json"] != intent["json"]
        )
    ):
        raise ApiError(
            409,
            "같은 주문 준비 요청의 내용이 다릅니다. 원래 요청의 결과를 먼저 확인해주세요.",
        )
    return row


def record_preparation(c, user_id, order_id, intent):
    if not intent:
        return None
    c.execute(
        "INSERT INTO checkout_preparations(user_id,request_key,request_hash,request_json,order_id,created_at) VALUES(?,?,?,?,?,?)",
        (user_id, intent["key"], intent["hash"], intent["json"], order_id, now_iso()),
    )
    return find_preparation(c, user_id, intent["key"])


def preparation_view(row):
    data = json.loads(row["request_json"])
    return {
        "requestKey": row["request_key"],
        "requestHash": row["request_hash"],
        "orderId": row["order_id"],
        "courseId": data["courseId"],
        "policyDigest": data["policyDigest"],
        "createdAt": row["created_at"],
    }
