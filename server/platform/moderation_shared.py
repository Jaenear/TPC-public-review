"""Current actors and immutable request receipts for community operations."""

import json
import re
from ..core import ApiError, new_id, now_iso
from ..moderation_records import canonical, digest
from .common import audit, require_role, roles
from .learning_start import active_member


def actor(c, user, role=None):
    user = active_member(c, user)
    if not user:
        raise ApiError(401, "로그인 후 이용해주세요.")
    if role:
        require_role(c, user, role)
    return user


def actor_role(c, user):
    return (
        "admin"
        if user["role"] == "admin"
        else "moderator" if "moderator" in roles(c, user) else "member"
    )


def limit(app, user, name, count=60):
    if not user:
        raise ApiError(401, "로그인 후 이용해주세요.")
    app.limiter.check((name, user["id"]), count, 3600)


def fields(data, allowed, required=()):
    if (
        not isinstance(data, dict)
        or set(data) - set(allowed)
        or set(required) - set(data)
    ):
        raise ApiError(400, "운영 요청 항목을 확인해주세요.")


def expected(data, name="expectedVersion", zero=False):
    value = data.get(name)
    if type(value) is not int or value < (0 if zero else 1) or value > 2147483647:
        raise ApiError(400, "기준 버전을 확인해주세요.")
    return value


def request(data, operation, target_id):
    key = data.get("requestKey")
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{12,120}", key):
        raise ApiError(400, "저장 요청 식별자를 확인해주세요.")
    return key, digest(
        {
            "operation": operation,
            "targetId": target_id,
            "data": {k: v for k, v in data.items() if k != "requestKey"},
        }
    )


def replay(c, user, key, hashed):
    found = c.execute(
        "SELECT result,http_status,request_hash FROM moderation_requests WHERE actor_id=? AND request_key=?",
        (user["id"], key),
    ).fetchone()
    if found:
        if found["request_hash"] != hashed:
            raise ApiError(409, "같은 요청 식별자로 다른 운영 처리를 할 수 없습니다.")
        return found["http_status"], json.loads(found["result"])
    return None


def remember(
    c,
    user,
    key,
    hashed,
    operation,
    target_id,
    receipt,
    *,
    action_id=None,
    decision_id=None,
    policy_id=None,
    conflict=False
):
    status = 409 if conflict else 200
    result = {"receipt": {**receipt, "requestKey": key}}
    if conflict:
        result.update(
            conflict=True,
            error="원문 또는 적용 규칙이 변경됐습니다. 최신 근거를 별도로 검토해주세요.",
        )
    c.execute(
        "INSERT INTO moderation_requests(id,actor_id,request_key,request_hash,operation,target_id,action_id,decision_id,policy_id,result,http_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            new_id("modrequest"),
            user["id"],
            key,
            hashed,
            operation,
            target_id,
            action_id,
            decision_id,
            policy_id,
            canonical(result),
            status,
            now_iso(),
        ),
    )
    return status, result


def audit_entry(c, user, action, kind, target_id, details):
    audit(c, user, action, kind, target_id, details)
    return c.execute(
        "SELECT id FROM audit_logs WHERE rowid=last_insert_rowid()"
    ).fetchone()[0]


def page(query):
    if set(query) - {"page", "status"} or any(len(v) != 1 for v in query.values()):
        raise ApiError(400, "운영 조회 조건을 확인해주세요.")
    try:
        number = int(query.get("page", ["1"])[0])
    except (TypeError, ValueError):
        raise ApiError(400, "페이지 번호를 확인해주세요.")
    if not 1 <= number <= 10000:
        raise ApiError(400, "페이지 번호를 확인해주세요.")
    return number, 20, (number - 1) * 20
