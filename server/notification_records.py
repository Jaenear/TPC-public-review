"""Immutable notification intent; site delivery and read state stay separate.

Historical rows are observed as baseline, never assigned invented business keys.
Email and marketing delivery remain explicitly unconfigured.
"""

import hashlib
import json
import re
import secrets
from urllib.parse import urlsplit
from .core import ApiError, new_id, now_iso

NOTIFICATION_TABLES = {
    "notification_intents",
    "notification_preferences",
    "notification_operations",
    "notification_signing_keys",
}
CATEGORIES = (
    "learning",
    "conversation",
    "transaction",
    "operation",
    "marketing",
    "legacy",
)
CHANNELS = ("site", "email")
REFERENCE_TABLES = {
    "question": "questions",
    "post": "posts",
    "comment": "comments",
    "moderation_action": "moderation_actions",
    "moderation_appeal": "moderation_appeals",
    "staff_action": "moderation_actions",
    "staff_appeal": "moderation_appeals",
    "course_staff": "courses",
    "revision": "lesson_revisions",
    "contract": "partner_contracts",
    "purchase": "purchase_orders",
    "enrollment": "enrollments",
    "booking": "bookings",
    "report": "reports",
    "own": "users",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def default_enabled(category, channel):
    return channel == "site" and category != "marketing"


def reference_from_href(href, recipient):
    """Interpret only known local routes. This is an observation, not an event."""
    raw = href[1:] if isinstance(href, str) and href.startswith("#") else href
    if not isinstance(raw, str) or not raw.startswith("/") or raw.startswith("//"):
        return "unknown", None, "legacy", "#/inbox"
    url = urlsplit(raw)
    if url.scheme or url.netloc or url.fragment or any(ord(ch) < 32 for ch in raw):
        return "unknown", None, "legacy", "#/inbox"
    patterns = [
        (r"/qa/([a-zA-Z0-9_-]+)", "question", "learning"),
        (r"/community/post/([a-zA-Z0-9_-]+)", "post", "conversation"),
        (
            r"/community/moderation/actions/([a-zA-Z0-9_-]+)",
            "moderation_action",
            "operation",
        ),
        (
            r"/community/moderation/appeals/([a-zA-Z0-9_-]+)",
            "moderation_appeal",
            "operation",
        ),
        (r"/manage/moderation/actions/([a-zA-Z0-9_-]+)", "staff_action", "operation"),
        (r"/manage/moderation/appeals/([a-zA-Z0-9_-]+)", "staff_appeal", "operation"),
        (
            r"/studio/course/([a-zA-Z0-9_-]+)/(?:curriculum|pricing|publication)",
            "course_staff",
            "operation",
        ),
        (r"/studio/revisions/([a-zA-Z0-9_-]+)", "revision", "operation"),
        (r"/studio/contracts/([a-zA-Z0-9_-]+)", "contract", "operation"),
    ]
    for pattern, kind, category in patterns:
        matched = re.fullmatch(pattern, url.path)
        if matched:
            return kind, matched[1], category, "#" + raw
    if url.path in (
        "/my",
        "/purchases",
        "/inbox",
        "/community",
        "/community/moderation/actions",
        "/community/moderation/appeals",
    ):
        return "own", recipient, "legacy", "#" + raw
    return "unknown", None, "legacy", "#/inbox"


def preference(c, user_id, category, channel):
    row = c.execute(
        "SELECT * FROM notification_preferences WHERE user_id=? AND category=? AND channel=?",
        (user_id, category, channel),
    ).fetchone()
    return {
        "category": category,
        "channel": channel,
        "enabled": bool(row["enabled"]) if row else default_enabled(category, channel),
        "version": row["version"] if row else 0,
        "origin": "member" if row else "product_default",
        "updatedAt": row["updated_at"] if row else None,
        "deliveryReady": channel == "site" and category != "marketing",
        "marketingConsentGranted": False,
    }


def emit(
    c,
    user_id,
    title,
    body,
    href,
    *,
    event_key,
    category,
    reference_kind,
    reference_id,
    group_key=None,
    channel="site"
):
    """Called inside the business writer transaction with a genuine event key."""
    if not user_id:
        return None
    if (
        category not in CATEGORIES
        or category == "legacy"
        or channel not in CHANNELS
        or not isinstance(event_key, str)
        or not re.fullmatch(r"[A-Za-z0-9_:/.-]{12,240}", event_key)
        or reference_kind not in REFERENCE_TABLES
        or not isinstance(reference_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", reference_id)
        or (
            group_key is not None
            and (
                not isinstance(group_key, str)
                or not 1 <= len(group_key) <= 240
                or any(ord(ch) < 32 for ch in group_key)
            )
        )
        or not isinstance(title, str)
        or not 1 <= len(title) <= 300
        or not isinstance(body, str)
    ):
        raise ApiError(400, "알림 사건의 유형·참조·식별자를 확인해주세요.")
    table = REFERENCE_TABLES[reference_kind]
    if not c.execute(
        "SELECT 1 FROM " + table + " WHERE id=?", (reference_id,)
    ).fetchone():
        raise ApiError(409, "알림 사건의 원래 참조를 확인해주세요.")
    if not c.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
        raise ApiError(409, "알림을 받을 회원을 확인해주세요.")
    _, _, _, safe_href = reference_from_href(href, user_id)
    payload = {
        "title": title,
        "body": body[:1000],
        "href": safe_href,
        "category": category,
        "referenceKind": reference_kind,
        "referenceId": reference_id,
        "groupKey": group_key,
        "channel": channel,
    }
    hashed = digest(payload)
    found = c.execute(
        "SELECT * FROM notification_intents WHERE recipient_id=? AND event_key=? AND channel=?",
        (user_id, event_key, channel),
    ).fetchone()
    if found:
        if found["payload_hash"] != hashed:
            raise ApiError(409, "같은 알림 사건에 다른 내용을 전달할 수 없습니다.")
        return {
            "intentId": found["id"],
            "notificationId": found["notification_id"],
            "decision": found["decision"],
        }
    pref = preference(c, user_id, category, channel)
    decision = (
        "preference_off"
        if not pref["enabled"]
        else (
            "marketing_unconfirmed"
            if category == "marketing"
            else "external_unconfigured" if channel != "site" else "site_created"
        )
    )
    intent_id, at = new_id("noticeintent"), now_iso()
    notice_id = new_id("notice") if decision == "site_created" else None
    c.execute(
        "INSERT INTO notification_intents(id,recipient_id,event_key,category,channel,origin,reference_kind,reference_id,group_key,payload_hash,decision,preference_version,notification_id,observed_at) VALUES(?,?,?,?,?,'event',?,?,?,?,?,?,?,?)",
        (
            intent_id,
            user_id,
            event_key,
            category,
            channel,
            reference_kind,
            reference_id,
            group_key,
            hashed,
            decision,
            pref["version"],
            notice_id,
            at,
        ),
    )
    if notice_id:
        c.execute(
            "INSERT INTO notifications(id,user_id,title,body,href,created_at) VALUES(?,?,?,?,?,?)",
            (notice_id, user_id, title, body[:1000], safe_href, at),
        )
    return {"intentId": intent_id, "notificationId": notice_id, "decision": decision}


def backfill(c):
    c.execute(
        "INSERT INTO notification_signing_keys(id,secret,created_at) VALUES('v1',?,?)",
        (secrets.token_bytes(32), now_iso()),
    )
    cursor = c.execute("SELECT * FROM notifications ORDER BY created_at,id")
    names = [d[0] for d in cursor.description]
    for values in cursor.fetchall():
        row = dict(zip(names, values))
        kind, ref, category, _ = reference_from_href(row["href"], row["user_id"])
        c.execute(
            "INSERT INTO notification_intents(id,recipient_id,event_key,category,channel,origin,reference_kind,reference_id,payload_hash,decision,notification_id,observed_at) VALUES(?,?,?,?,'site','baseline',?,?,?,'site_created',?,?)",
            (
                "noticebaseline_" + row["id"],
                row["user_id"],
                "baseline/" + row["id"],
                category,
                kind,
                ref,
                digest(
                    {
                        k: row[k]
                        for k in (
                            "id",
                            "user_id",
                            "title",
                            "body",
                            "href",
                            "created_at",
                        )
                    }
                ),
                row["id"],
                now_iso(),
            ),
        )
