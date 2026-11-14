"""Comment relationships and explicit personal conversation boundaries.

Existing comments receive metadata only. A first explicit edit may observe its
previous body; migration and GET never invent prior revisions or block intent.
"""

import hashlib
import json
import re
from .core import ApiError, now_iso

COMMUNITY_TABLES = {
    "community_comment_heads",
    "community_comment_revisions",
    "community_comment_operations",
    "community_block_heads",
    "community_block_operations",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def body_hash(body):
    return hashlib.sha256(body.encode()).hexdigest()


def fields(data, allowed, required=()):
    if (
        not isinstance(data, dict)
        or set(data) - set(allowed)
        or set(required) - set(data)
    ):
        raise ApiError(400, "대화 요청 항목을 확인해주세요.")


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", value):
        raise ApiError(400, "대화 대상 식별자를 확인해주세요.")
    return value


def request_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{12,120}", value):
        raise ApiError(400, "저장 요청 식별자를 확인해주세요.")
    return value


def expected(value, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value < 2147483647:
        raise ApiError(400, "댓글/차단의 기준 판본을 확인해주세요.")
    return value


def blocked_pair(c, first, second):
    if not first or not second or first == second:
        return False
    return bool(
        c.execute(
            "SELECT 1 FROM community_block_heads WHERE blocked=1 AND ((user_id=? AND target_id=?) OR (user_id=? AND target_id=?)) LIMIT 1",
            (first, second, second, first),
        ).fetchone()
    )


def visibility_sql(user, alias="cm"):
    # alias is an internal SQL identifier, never a request value.
    assert alias in ("cm", "comment")
    if not user:
        return "", []
    return (
        " AND NOT EXISTS(SELECT 1 FROM community_block_heads cb WHERE cb.blocked=1 AND ((cb.user_id=? AND cb.target_id="
        + alias
        + ".user_id) OR (cb.target_id=? AND cb.user_id="
        + alias
        + ".user_id)))",
        [user["id"], user["id"]],
    )


def backfill(c):
    for cid, pid in c.execute("SELECT id,post_id FROM comments ORDER BY id").fetchall():
        c.execute(
            "INSERT INTO community_comment_heads(comment_id,post_id,origin,observed_at) VALUES(?,?,'baseline',?)",
            (cid, pid, now_iso()),
        )
