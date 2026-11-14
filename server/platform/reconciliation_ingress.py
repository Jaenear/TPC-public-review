"""Toss raw-byte HMAC authentication and bounded, unambiguous event parsing."""

import base64
import hashlib
import hmac
import json
import re
import time

from ..core import ApiError
from .reconciliation_contract import PG_STATUSES, Scope, document, hashed, opaque

MAX_BYTES = 65536


def security_keys(value):
    """Official security keys are 64 hex chars. Invalid configuration is closed.

    This is distinct from API secret keys. Accept up to three rotating keys;
    neither key material nor signature headers are persisted or returned.
    """
    if not isinstance(value, str) or not value:
        return ()
    items = [item.strip() for item in value.split(",")]
    if not 1 <= len(items) <= 3 or any(not re.fullmatch(r"[A-Fa-f0-9]{64}", item) for item in items):
        return ()
    return tuple(bytes.fromhex(item) for item in items)


def configured_scope(gateway):
    mid = getattr(gateway, "merchant_id", None)
    if not gateway.ready or gateway.mode not in ("test", "live") or not isinstance(mid, str) or not 1 <= len(mid) <= 200:
        return None
    return Scope.toss(gateway.mode, mid)


def single_header(headers, key, maximum):
    values = headers.get_all(key, [])
    if len(values) != 1 or not isinstance(values[0], str) or not 1 <= len(values[0]) <= maximum:
        raise ApiError(403, "웹훅 인증을 확인할 수 없습니다.")
    return values[0]


def verify_signature(raw, headers, keys):
    """Keep signed bytes/time exact. Transmission IDs are not dedupe keys.

    Replay safety is durable event/content identity plus a fresh PG lookup.
    The docs do not specify a timestamp syntax/TTL, so no invented time format
    is imposed on this signed header. It is never used as financial truth.
    """
    if not keys or any(not isinstance(key, bytes) or len(key) != 32 for key in keys):
        raise ApiError(503, "웹훅 수신 환경을 준비 중입니다.")
    stamp = single_header(headers, "tosspayments-webhook-transmission-time", 128)
    signatures = single_header(headers, "tosspayments-webhook-signature", 4096)
    try:
        suffix = stamp.encode("ascii")
        parts = signatures.split(",")
        if len(parts) > 64:
            raise ValueError()
        digests = []
        for item in parts:
            version, separator, encoded = item.strip().partition(":")
            if version != "v1" or not separator:
                continue  # Unknown future versions do not invalidate a valid v1.
            try:
                digest = base64.b64decode(encoded, validate=True)
            except ValueError:
                continue
            if len(digest) == 32:
                digests.append(digest)
        expected = [hmac.new(key, raw + b":" + suffix, hashlib.sha256).digest() for key in keys]
        valid = False
        for wanted in expected:
            for actual in digests:
                valid |= hmac.compare_digest(wanted, actual)
        if not valid:
            raise ValueError()
        return {"rawBodyHash": hashlib.sha256(raw).hexdigest(), "transmissionTimeHash": hashed(stamp)}
    except (ValueError, UnicodeError):
        raise ApiError(403, "웹훅 인증을 확인할 수 없습니다.") from None


def read_raw(h):
    if h.headers.get("Transfer-Encoding"):
        raise ApiError(400, "웹훅 전송 형식을 확인해주세요.")
    values = h.headers.get_all("Content-Length", [])
    if len(values) != 1 or not re.fullmatch(r"[0-9]{1,8}", values[0]):
        raise ApiError(400, "웹훅 요청 크기를 확인해주세요.")
    size = int(values[0])
    if not 1 <= size <= MAX_BYTES:
        raise ApiError(413, "웹훅 크기 한도를 초과했습니다.")
    types = h.headers.get_all("Content-Type", [])
    if len(types) != 1 or types[0].split(";")[0].strip().lower() != "application/json":
        raise ApiError(415, "웹훅은 JSON 형식으로 전달해주세요.")
    previous = h.connection.gettimeout()
    try:
        deadline = time.monotonic() + 3
        chunks, remaining = [], size
        while remaining:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                raise TimeoutError()
            h.connection.settimeout(timeout)
            chunk = h.rfile.read1(min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    except (TimeoutError, OSError):
        raise ApiError(408, "웹훅 본문 수신 시간을 초과했습니다.") from None
    finally:
        h.connection.settimeout(previous)
    if len(raw) != size:
        raise ApiError(400, "웹훅 본문 수신이 완료되지 않았습니다.")
    return raw


def parse_event(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError()
            value[key] = item
        return value

    def invalid_number(value):
        raise ValueError()

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid_number)
        document(payload)
    except (ValueError, UnicodeError, RecursionError):
        raise ApiError(400, "웹훅 JSON 형식을 확인해주세요.") from None
    kind = payload.get("eventType")
    # The official deposit callback is a flat body without eventType.
    if kind is None and all(key in payload for key in ("orderId", "status", "secret", "transactionKey")):
        kind = "DEPOSIT_CALLBACK"
    if kind not in ("PAYMENT_STATUS_CHANGED", "DEPOSIT_CALLBACK", "CANCEL_STATUS_CHANGED"):
        raise ApiError(400, "지원하는 결제 웹훅 유형을 확인해주세요.")
    body = payload if kind == "DEPOSIT_CALLBACK" else payload.get("data")
    if not isinstance(body, dict):
        raise ApiError(400, "웹훅의 결제 참조를 확인해주세요.")
    # Cancel is not a Payment object: do not invent a required orderId there.
    reference = body.get("orderId")
    if reference is not None:
        reference = opaque(reference)
    if kind != "CANCEL_STATUS_CHANGED" and reference is None:
        raise ApiError(400, "웹훅의 주문 참조를 확인해주세요.")
    identity = payload.get("eventId")
    if "eventId" in payload:
        identity = opaque(identity, 64)
    key = body.get("paymentKey")
    if key is not None:
        key = opaque(key, 200)
    secret = body.get("secret")
    if secret is not None:
        secret = opaque(secret, 50)
    if kind == "DEPOSIT_CALLBACK" and secret is None:
        raise ApiError(400, "입금 웹훅의 검증 참조를 확인해주세요.")
    transaction = body.get("transactionKey")
    if transaction is not None:
        transaction = opaque(transaction, 64)
    mid = body.get("mId")
    if mid is not None:
        mid = opaque(mid, 200)
    status = body.get("status")
    hint = status if isinstance(status, str) and status in PG_STATUSES else "UNKNOWN"
    return {"type": kind, "payload": payload, "reference": reference, "identity": identity,
            "keyHash": hashed(key) if key else None, "secretHash": hashed(secret) if secret else None,
            "transactionHash": hashed(transaction) if transaction else None,
            "merchantHash": hashed(mid) if mid else None, "hint": hint}
