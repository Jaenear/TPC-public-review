"""Read-only refund evidence, not an eligibility rule or a cancellation command.

Purchase documents are immutable references. Learning facts belong to a member
and course across purchases; a saved position does not prove consumed time.
All queries share one SQLite read transaction and never call the gateway.
"""

import json
import re

from ..access import for_member, grant_state
from ..core import ApiError, now_iso
from .common import require_role
from .order_products import active_actor, purchase_snapshot
from .policies import PUBLIC_KINDS, content_hash, digest
from .pricing import policy_link

VIDEO_LIMIT = 30
POLICY_KINDS = ("refund", "course_terms")
ORDER_FIELDS = {
    "id": "id", "courseId": "course_id", "title": "title", "amount": "amount",
    "currency": "currency", "mode": "mode", "status": "status", "version": "version",
    "createdAt": "created_at", "updatedAt": "updated_at", "paidAt": "paid_at",
}


def policy_evidence(c, order):
    """Only the purchase's public refund/provision originals leave this function."""
    empty = {"status": "unrecorded", "sealedAt": None, "fingerprint": None, "documents": []}
    sealed = c.execute("SELECT * FROM order_policy_sets WHERE order_id=?", (order["id"],)).fetchone()
    if not sealed:
        return empty
    try:
        original = json.loads(sealed["course_snapshot"])
        if (not isinstance(original, dict) or original.get("id") != order["course_id"]
                or original.get("title") != order["title"] or original.get("amount") != order["amount"]
                or original.get("currency") != order["currency"]
                or type(original.get("version")) is not int):
            raise ValueError("Purchase identity mismatch")
        # Also validates typed price/product/consent seals when present. Legacy
        # descriptors are never fabricated for older versioned policy records.
        purchase_snapshot(c, order)
        rows = c.execute(
            "SELECT p.*,d.kind AS sealed_kind,d.content_hash AS sealed_hash,d.visibility "
            "FROM order_policy_documents d JOIN policy_versions p ON p.id=d.policy_version_id "
            "WHERE d.order_id=? AND d.visibility='public' ORDER BY d.kind", (order["id"],)
        ).fetchall()
        consents = c.execute(
            "SELECT policy_version_id,content_hash,accepted_at FROM consents "
            "WHERE order_id=? AND user_id=? AND context='purchase'", (order["id"], order["user_id"])
        ).fetchall()
        if (len(rows) != len(PUBLIC_KINDS) or {r["kind"] for r in rows} != set(PUBLIC_KINDS)
                or {(s["policy_version_id"], s["content_hash"]) for s in consents}
                != {(r["id"], r["sealed_hash"]) for r in rows}):
            raise ValueError("Purchase consent mismatch")
        accepted = {s["policy_version_id"]: s["accepted_at"] for s in consents}
        for row in rows:
            expected_course = order["course_id"] if row["kind"] == "course_terms" else None
            if (row["kind"] != row["sealed_kind"] or row["mode"] != order["mode"]
                    or row["course_id"] != expected_course
                    or (expected_course and row["course_version"] != original["version"])
                    or row["content_hash"] != row["sealed_hash"]
                    or content_hash(row, policy_link(c, row["id"])) != row["sealed_hash"]
                    or not row["published_at"]):
                raise ValueError("Purchase document mismatch")
        documents = [{
            "id": row["id"], "kind": row["kind"], "edition": row["edition"],
            "title": row["title"], "summary": row["summary"], "body": row["body"],
            "contentHash": row["sealed_hash"], "acceptedAt": accepted[row["id"]],
            "effectiveAt": row["effective_at"], "expiresAt": row["expires_at"],
        } for row in rows if row["kind"] in POLICY_KINDS]
        return {"status": "recorded", "sealedAt": sealed["sealed_at"],
                "fingerprint": sealed["fingerprint"], "documents": documents}
    except (ValueError, TypeError, KeyError, ApiError):
        return {**empty, "status": "inconsistent"}


def learning_evidence(c, order):
    owner = (order["user_id"], order["course_id"])
    complete = c.execute("SELECT COUNT(*),MAX(completed_at) FROM progress WHERE user_id=? AND course_id=?", owner).fetchone()
    quiz = c.execute("SELECT COUNT(*),MAX(created_at) FROM lesson_quiz_attempts WHERE user_id=? AND course_id=?", owner).fetchone()
    progress_at = c.execute("SELECT MAX(created_at) FROM lesson_progress_events WHERE user_id=? AND course_id=?", owner).fetchone()[0]
    video = c.execute("SELECT COUNT(*),MAX(updated_at) FROM lesson_video_progress WHERE user_id=? AND course_id=?", owner).fetchone()
    current = c.execute(
        "SELECT COUNT(*) FROM lesson_video_progress v JOIN course_lessons l ON l.course_id=v.course_id AND l.id=v.lesson_id "
        "WHERE v.user_id=? AND v.course_id=? AND v.asset_id=l.video_asset_id", owner
    ).fetchone()[0]
    legacy = c.execute(
        "SELECT COUNT(*) FROM lesson_watch w LEFT JOIN lesson_watch_sources s ON s.user_id=w.user_id AND s.course_id=w.course_id AND s.lesson_id=w.lesson_id "
        "WHERE w.user_id=? AND w.course_id=? AND NOT EXISTS(SELECT 1 FROM lesson_video_progress v "
        "WHERE v.user_id=w.user_id AND v.course_id=w.course_id AND v.lesson_id=w.lesson_id AND v.asset_id=s.asset_id)", owner
    ).fetchone()[0]
    watch_at = c.execute("SELECT MAX(updated_at) FROM lesson_watch WHERE user_id=? AND course_id=?", owner).fetchone()[0]
    rows = c.execute(
        "SELECT v.lesson_id,v.asset_id,v.seconds,v.furthest_seconds,v.updated_at,l.title,l.video_asset_id,"
        "CASE WHEN m.asset_sha256=a.sha256 THEN m.duration_seconds ELSE NULL END AS duration "
        "FROM lesson_video_progress v JOIN course_lessons l ON l.course_id=v.course_id AND l.id=v.lesson_id "
        "JOIN media_assets a ON a.id=v.asset_id LEFT JOIN media_playback_metadata m ON m.asset_id=v.asset_id "
        "WHERE v.user_id=? AND v.course_id=? ORDER BY v.updated_at DESC,v.lesson_id,v.asset_id LIMIT ?", (*owner, VIDEO_LIMIT)
    ).fetchall()
    records = []
    for row in rows:
        duration = row["duration"]
        if duration and row["furthest_seconds"] > duration + 1:
            duration = None
        records.append({
            "lessonId": row["lesson_id"], "title": row["title"],
            "source": "current" if row["asset_id"] == row["video_asset_id"] else "previous",
            "resumeSeconds": row["seconds"], "furthestSeconds": row["furthest_seconds"],
            "durationSeconds": duration,
            "furthestPercent": round(min(row["furthest_seconds"], duration) / duration * 100, 2) if duration else None,
            "updatedAt": row["updated_at"],
        })
    dates = [d for d in [complete[1], quiz[1], progress_at, video[1], watch_at] if d]
    return {"scope": "member_course_lifetime", "orderAttribution": "unavailable",
            "completedLessons": complete[0], "quizAttempts": quiz[0], "totalVideoRecords": video[0],
            "currentVideoSources": current, "legacyResumeRecords": legacy,
            "latestRecordedAt": max(dates) if dates else None, "videoRecords": records,
            "videoLimit": VIDEO_LIMIT, "hasMoreVideos": video[0] > len(records)}


def access_evidence(c, order, at):
    rows = c.execute(
        "SELECT g.* FROM enrollment_grants g JOIN enrollments e ON e.id=g.enrollment_id "
        "WHERE e.user_id=? AND e.course_id=?", (order["user_id"], order["course_id"])
    ).fetchall()
    def counts(target):
        found = [r for r in rows if (r["source"] == "payment" and r["source_id"] == order["id"]) == target]
        states = [grant_state(r, at) for r in found]
        return {"total": len(found), **{s: states.count(s) for s in ("active", "scheduled", "expired", "revoked")}}
    access = for_member(c, order["user_id"], order["course_id"], at)
    return {"status": access["status"], "canAccess": access["canAccess"],
            "orderGrants": counts(True), "otherGrants": counts(False), "revokeScope": "payment_order_only"}


def payment_evidence(c, order):
    def last(kind, status):
        row = c.execute("SELECT created_at FROM payment_events WHERE order_id=? AND event_type=? AND status=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (order["id"], kind, status)).fetchone()
        return row[0] if row else None
    return {"verifiedApprovalAt": last("confirm", "verified"),
            "verifiedCancellationAt": last("refund", "verified"),
            "unverifiedCancellationAt": last("refund", "unverified")}


def review_evidence(order, policy, inquiry, learning, access):
    state = ("cancellation_pending" if order["status"] == "refunding" else
             "cancellation_verified" if order["status"] == "refunded" else
             "payment_not_completed" if order["status"] != "paid" else
             "records_need_review" if policy["status"] != "recorded" else "operator_review_required")
    return {"state": state, "automaticEligibility": None, "checklist": [
        {"id": "original_terms", "status": policy["status"]},
        {"id": "inquiry", "status": inquiry["status"] if inquiry else "none"},
        {"id": "payment_result", "status": order["status"]},
        {"id": "learning_scope", "status": "lifetime_records" if learning["latestRecordedAt"] else "no_records"},
        {"id": "access_scope", "status": "order_grants_recorded" if access["orderGrants"]["total"] else "order_grants_missing"},
        {"id": "commercial_decision", "status": "operator_review_required"},
    ]}


def context_value(c, actor, order, operator=False, at=None):
    at = at or now_iso()
    policy, learning, access = policy_evidence(c, order), learning_evidence(c, order), access_evidence(c, order, at)
    request = c.execute("SELECT * FROM refund_requests WHERE order_id=? AND user_id=?", (order["id"], order["user_id"])).fetchone()
    inquiry = {key: request[column] for key, column in {
        "status": "status", "reason": "reason", "resolution": "resolution", "createdAt": "created_at", "updatedAt": "updated_at"
    }.items()} if request else None
    value = {"schemaVersion": 1, "audience": "operator" if operator else "member", "viewerId": actor["id"],
             "order": {key: order[column] for key, column in ORDER_FIELDS.items()},
             "policy": policy, "inquiry": inquiry, "learning": learning, "access": access,
             "paymentEvidence": payment_evidence(c, order),
             "review": review_evidence(order, policy, inquiry, learning, access)}
    return {**value, "contextHash": digest(value), "observedAt": at}


class RefundContextService:
    def __init__(self, app):
        self.app = app

    def read(self, user, order_id, operator=False):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor = active_actor(c, user)
            if operator:
                require_role(c, actor, "admin")
            order = c.execute(
                "SELECT * FROM purchase_orders WHERE id=?" + ("" if operator else " AND user_id=?"),
                (order_id,) if operator else (order_id, actor["id"])
            ).fetchone()
            if not order:
                raise ApiError(404, "조회할 수 있는 주문을 찾을 수 없습니다.")
            return {"refundContext": context_value(c, actor, order, operator)}

    def route(self, h, path, method, query):
        match = re.fullmatch(r"/api/(checkout/orders|manage/purchases)/([a-z0-9_]+)/refund-context", path)
        if not match:
            return None
        if method != "GET":
            raise ApiError(405, "환불 검토 근거는 조회로만 제공됩니다.")
        return 200, self.read(h.user, match.group(2), operator=match.group(1) == "manage/purchases"), {}
