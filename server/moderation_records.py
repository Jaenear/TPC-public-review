"""Minimal content observations; no invented past rule, actor or decision.

Only IDs, versions, flags and content hashes are frozen. Private old bodies are
not copied into the moderation ledger while retention rules remain undecided.
"""

import hashlib
import json
from .core import ApiError, now_iso

MODERATION_TABLES = {
    "moderation_comment_heads",
    "moderation_report_observations",
    "moderation_policy_versions",
    "moderation_policy_states",
    "moderation_actions",
    "moderation_appeals",
    "moderation_decisions",
    "moderation_requests",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def row(c, sql, values=()):
    cursor = c.execute(sql, values)
    value = cursor.fetchone()
    return dict(zip([d[0] for d in cursor.description], value)) if value else None


def observe(c, kind, target_id):
    if kind == "post":
        item = row(c, "SELECT * FROM posts WHERE id=?", (target_id,))
        if not item:
            raise ApiError(404, "운영 대상 게시글을 찾을 수 없습니다.")
        content = {k: item[k] for k in ("id", "board_id", "user_id", "title", "body")}
        return {
            "kind": kind,
            "id": item["id"],
            "postId": item["id"],
            "userId": item["user_id"],
            "postUserId": item["user_id"],
            "boardId": item["board_id"],
            "version": item["version"],
            "contentHash": digest(content),
            "status": item["status"],
            "locked": bool(item["locked"]),
            "pinned": bool(item["pinned"]),
        }
    if kind != "comment":
        raise ApiError(404, "운영 대상을 찾을 수 없습니다.")
    item = row(
        c,
        "SELECT cm.*,p.board_id,p.user_id AS post_user_id,h.version,h.owner_deleted,h.origin,h.observed_at FROM comments cm JOIN posts p ON p.id=cm.post_id JOIN moderation_comment_heads h ON h.comment_id=cm.id WHERE cm.id=?",
        (target_id,),
    )
    if not item:
        raise ApiError(404, "운영 대상 댓글을 찾을 수 없습니다.")
    parent = observe(c, "post", item["post_id"])
    return {
        "kind": kind,
        "id": item["id"],
        "postId": item["post_id"],
        "userId": item["user_id"],
        "postUserId": item["post_user_id"],
        "boardId": item["board_id"],
        "version": item["version"],
        "contentHash": digest(
            {k: item[k] for k in ("id", "post_id", "user_id", "body")}
        ),
        "contextHash": parent["contentHash"],
        "status": item["status"],
        "ownerDeleted": bool(item["owner_deleted"]),
        "postStatus": parent["status"],
        "postLocked": parent["locked"],
        "counterOrigin": item["origin"],
        "counterObservedAt": item["observed_at"],
    }


def initialize_comment(c, comment_id, origin="created"):
    c.execute(
        "INSERT OR IGNORE INTO moderation_comment_heads(comment_id,origin,observed_at) VALUES(?,?,?)",
        (comment_id, origin, now_iso()),
    )


def observe_report(c, report_id, origin="created"):
    report = row(c, "SELECT * FROM reports WHERE id=?", (report_id,))
    if not report:
        raise ApiError(404, "신고를 찾을 수 없습니다.")
    item = observe(c, "post", report["post_id"])
    c.execute(
        "INSERT OR IGNORE INTO moderation_report_observations(report_id,post_id,origin,snapshot,content_hash,observed_at) VALUES(?,?,?,?,?,?)",
        (
            report_id,
            item["postId"],
            origin,
            canonical(item),
            item["contentHash"],
            now_iso(),
        ),
    )


def backfill(c):
    for item in c.execute("SELECT id FROM comments ORDER BY id").fetchall():
        initialize_comment(c, item[0], "baseline")
    for item in c.execute("SELECT id FROM reports ORDER BY id").fetchall():
        observe_report(c, item[0], "baseline")
