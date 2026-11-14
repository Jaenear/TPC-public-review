"""Source-specific grants; learning history never doubles as authorization.

Call writers inside BEGIN IMMEDIATE. Dates are canonical UTC seconds. Derived
states are evaluated on every read, so expiry does not depend on a background job.
The pre-v6 enrollment_access table is retained as an unchanged migration snapshot.
"""

from datetime import datetime, timezone
import hashlib
import json
import re

from .core import ApiError, new_id, now_iso


def canonical_time(value, label):
    if (
        not isinstance(value, str)
        or len(value) > 40
        or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})",
            value,
        )
    ):
        raise ApiError(400, label + "에 시간대가 포함된 날짜와 시각을 입력해주세요.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (
            result.astimezone(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    except (ValueError, OverflowError):
        raise ApiError(400, label + "의 날짜와 시각을 확인해주세요.")


def period(data, defaults=None):
    start_default, end_default = defaults or (now_iso(), None)
    start = data.get("startsAt", start_default)
    if start is None or start == "":
        start = start_default
    end = data.get("expiresAt", end_default)
    start = canonical_time(start, "시작 시각")
    end = None if end is None or end == "" else canonical_time(end, "종료 시각")
    if end is not None and end <= start:
        raise ApiError(400, "종료 시각은 시작 시각보다 뒤여야 합니다.")
    return start, end


def ensure_enrollment(c, user_id, course_id):
    c.execute(
        "INSERT OR IGNORE INTO enrollments(id,user_id,course_id,created_at) VALUES(?,?,?,?)",
        (new_id("enr"), user_id, course_id, now_iso()),
    )
    return c.execute(
        "SELECT * FROM enrollments WHERE user_id=? AND course_id=?",
        (user_id, course_id),
    ).fetchone()


def ensure_source_enrollment(c, user_id, course_id, source="free", source_id=""):
    """Shared domain writer; HTTP serialization and worker startup stay outside."""
    row = ensure_enrollment(c, user_id, course_id)
    existing = c.execute("SELECT 1 FROM enrollment_grants WHERE enrollment_id=? LIMIT 1", (row["id"],)).fetchone()
    if source != "free" or not existing:
        grant(c, row["id"], source, source_id)
    return row


def grant(
    c,
    enrollment_id,
    source,
    source_id,
    reason="",
    starts_at=None,
    expires_at=None,
    actor_id=None,
):
    existing = c.execute(
        "SELECT * FROM enrollment_grants WHERE enrollment_id=? AND source=? AND source_id=?",
        (enrollment_id, source, source_id),
    ).fetchone()
    if existing:
        # A replay must never revive a revoked grant or extend its period.
        if source == "manual" and (
            existing["reason"],
            existing["starts_at"],
            existing["expires_at"],
        ) != (reason, starts_at, expires_at):
            raise ApiError(
                409,
                "이미 처리한 발급 요청과 내용이 다릅니다. 수강권 목록을 다시 확인해주세요.",
            )
        return existing
    grant_id, timestamp = new_id("grant"), now_iso()
    if starts_at is None:
        starts_at = timestamp
    c.execute(
        "INSERT INTO enrollment_grants(id,enrollment_id,source,source_id,starts_at,expires_at,reason,created_by,updated_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            grant_id,
            enrollment_id,
            source,
            source_id,
            starts_at,
            expires_at,
            reason,
            actor_id,
            actor_id,
            timestamp,
            timestamp,
        ),
    )
    return c.execute(
        "SELECT * FROM enrollment_grants WHERE id=?", (grant_id,)
    ).fetchone()


def grant_state(row, at):
    if row["status"] == "revoked":
        return "revoked"
    if row["expires_at"] is not None and row["expires_at"] <= at:
        return "expired"
    if row["starts_at"] is not None and row["starts_at"] > at:
        return "scheduled"
    return "active"


def public_grant(row, at):
    return {
        "id": row["id"],
        "source": row["source"],
        "reference": row["source_id"] if row["source"] in ("payment", "pilot") else "",
        "status": grant_state(row, at),
        "startsAt": row["starts_at"],
        "expiresAt": row["expires_at"],
        "reason": row["reason"],
        "revocationReason": row["revocation_reason"],
        "revokedAt": row["revoked_at"],
        "version": row["version"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def summary(c, enrollment_id, at=None):
    at = at or now_iso()
    rows = c.execute(
        "SELECT * FROM enrollment_grants WHERE enrollment_id=? ORDER BY created_at,id",
        (enrollment_id,),
    ).fetchall()
    grants = [public_grant(row, at) for row in rows]
    states = {item["status"] for item in grants}
    state = next(
        (
            value
            for value in ("active", "scheduled", "expired", "revoked")
            if value in states
        ),
        "none",
    )
    # Merge touching intervals too: an immediate renewal has no artificial gap.
    end = None
    if state == "active":
        active = [row for row in rows if grant_state(row, at) == "active"]
        if all(row["expires_at"] is not None for row in active):
            end = max(row["expires_at"] for row in active)
            for row in sorted(
                (row for row in rows if grant_state(row, at) == "scheduled"),
                key=lambda row: row["starts_at"],
            ):
                if row["starts_at"] > end:
                    break
                if row["expires_at"] is None:
                    end = None
                    break
                end = max(end, row["expires_at"])
    next_start = min(
        (item["startsAt"] for item in grants if item["status"] == "scheduled"),
        default=None,
    )
    # Different historical grants may have different reasons, even when their
    # timestamps share a second. Never claim one of them explains every grant.
    reasons = {
        row["revocation_reason"]
        for row in rows
        if row["status"] == "revoked" and row["revocation_reason"]
    }
    reason = (
        (
            next(iter(reasons))
            if len(reasons) == 1
            else "출처별 수강권에서 각각의 회수 사유를 확인해주세요."
        )
        if reasons
        else ""
    )
    token = hashlib.sha256(
        json.dumps(sorted((row["id"], row["version"]) for row in rows)).encode()
    ).hexdigest()
    return {
        "status": state,
        "canAccess": state == "active",
        "expiresAt": end,
        "nextStartsAt": next_start,
        "grants": grants,
        "version": token,
        "asOf": at,
        "reason": reason if state == "revoked" else "",
    }


def for_member(c, user_id, course_id, at=None):
    row = c.execute(
        "SELECT id FROM enrollments WHERE user_id=? AND course_id=?",
        (user_id, course_id),
    ).fetchone()
    return summary(c, row["id"] if row else "", at)


def denial(access):
    return {
        "expired": "수강 기간이 종료되었습니다. 학습 기록은 보존되며 수강권은 운영자에게 문의해주세요.",
        "scheduled": "아직 수강 시작 전입니다. 마이 캠프에서 시작 시각을 확인해주세요.",
        "revoked": "수강권이 회수되었습니다. 학습 기록은 보존되며 운영자에게 문의할 수 있습니다.",
    }.get(access["status"], "활성 수강권이 필요한 강의입니다.")


def revoke(c, row, reason, actor_id=None):
    if row["status"] == "revoked":
        return False
    timestamp = now_iso()
    c.execute(
        "UPDATE enrollment_grants SET status='revoked',revocation_reason=?,revoked_at=?,updated_at=?,updated_by=?,version=version+1 WHERE id=? AND status='active'",
        (reason, timestamp, timestamp, actor_id, row["id"]),
    )
    return True
