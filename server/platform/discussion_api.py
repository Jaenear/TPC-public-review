"""HTTP content edits: scoped request receipts and notifications commit together."""

import json
import re
from ..core import ApiError, new_id, now_iso
from ..discussion_records import canonical, digest
from .common import can_manage_course, notify

API_TABLES = {
    "discussion_edit_requests",
    "question_attachment_payloads",
    "question_attachment_operations",
}


def request_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{12,120}", value):
        raise ApiError(400, "저장 요청 식별자를 확인해주세요.")
    return value


def actor_role(c, user, course):
    return (
        "admin"
        if user["role"] == "admin"
        else "coach" if can_manage_course(c, user, course) else "member"
    )


class DiscussionAPI:
    def __init__(self, app, discussions):
        self.app = app
        self.history = discussions.history

    def edit(self, user, kind, target_id, data):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        self.app.limiter.check(("qa-edit", user["id"]), 60, 3600)
        allowed = {"requestKey", "expectedVersion", "body", "reason", "attachments"} | (
            {"title"} if kind == "question" else set()
        )
        if set(data) - allowed:
            raise ApiError(400, "본문 수정 항목을 확인해주세요.")
        key = request_key(data.get("requestKey"))
        prepared = self.history.prepare(kind, data)
        hashed = digest({"kind": kind, "targetId": target_id, **prepared})
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, _, question, head = self.history.scope(
                c, user, kind, target_id, editing=True
            )
            receipt = c.execute(
                "SELECT * FROM discussion_edit_requests WHERE actor_id=? AND request_key=?",
                (user["id"], key),
            ).fetchone()
            if receipt:
                if receipt["request_hash"] != hashed:
                    raise ApiError(
                        409, "같은 요청 식별자로 다른 내용을 저장할 수 없습니다."
                    )
                result = json.loads(receipt["result"])
            else:
                for aid in prepared["attachments"]["add"]:
                    if not c.execute(
                        "SELECT 1 FROM question_attachment_payloads WHERE attachment_id=? AND question_id=?",
                        (aid, question["id"]),
                    ).fetchone():
                        raise ApiError(
                            409, "검사할 원본이 보관된 첨부만 연결할 수 있습니다."
                        )
                result = self.history.edit_in(c, user, kind, target_id, prepared)
                course = self.app.platform.discussions.course(c, question["course_id"])
                target = (
                    course["instructor_user_id"]
                    if user["id"] == question["user_id"]
                    else question["user_id"]
                )
                recipient = (
                    c.execute(
                        "SELECT * FROM users WHERE id=? AND status='active'", (target,)
                    ).fetchone()
                    if target
                    else None
                )
                if (
                    recipient
                    and target != user["id"]
                    and (
                        target == question["user_id"]
                        or can_manage_course(c, recipient, course)
                    )
                ):
                    notify(
                        c,
                        target,
                        "강의 질문·답변 내용이 수정되었습니다",
                        "현재 열람 권한으로 내용과 수정 이력을 확인해주세요.",
                        "#/qa/" + question["id"],
                        event_key="qa.edit:" + result["revisionId"],
                        category="learning",
                        reference_kind="question",
                        reference_id=question["id"],
                        group_key="question:" + question["id"],
                    )
                c.execute(
                    "INSERT INTO discussion_edit_requests(id,actor_id,actor_role,kind,target_id,question_id,request_key,request_hash,expected_version,result_version,question_revision_id,answer_revision_id,result,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        new_id("qaedit"),
                        user["id"],
                        actor_role(c, user, course),
                        kind,
                        target_id,
                        question["id"],
                        key,
                        hashed,
                        prepared["expectedVersion"],
                        result["contentVersion"],
                        result["revisionId"] if kind == "question" else None,
                        result["revisionId"] if kind == "answer" else None,
                        canonical(result),
                        now_iso(),
                    ),
                )
                head = c.execute(
                    f"SELECT version FROM {kind}_content_heads WHERE target_id=?",
                    (target_id,),
                ).fetchone()
            return {
                "edit": {**result, "requestKey": key},
                "currentContentVersion": head["version"],
            }

    def revisions(self, user, kind, target_id, query):
        if set(query) - {"beforeVersion", "limit"} or any(
            len(values) != 1 for values in query.values()
        ):
            raise ApiError(400, "수정 이력 조회 조건을 확인해주세요.")
        try:
            limit = int(query.get("limit", ["20"])[0])
            before = (
                int(query["beforeVersion"][0]) if "beforeVersion" in query else None
            )
        except (ValueError, TypeError):
            raise ApiError(400, "수정 이력 페이지를 확인해주세요.")
        if not 1 <= limit <= 50 or (
            before is not None and not 1 <= before <= 2147483647
        ):
            raise ApiError(400, "수정 이력 페이지를 확인해주세요.")
        result = self.history.read(user, kind, target_id, before, limit)
        # Snapshot first, then recheck current delivery/permission at this boundary.
        # Every subsequent file GET repeats that check in its own read transaction.
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            self.history.scope(c, user, kind, target_id, history_read=True)
            for revision in result["revisions"]:
                role = c.execute(
                    "SELECT actor_role FROM discussion_edit_requests WHERE question_revision_id=? OR answer_revision_id=?",
                    (revision["id"], revision["id"]),
                ).fetchone()
                revision["actorRole"] = role[0] if role else None
                for key in ("beforeAttachments", "afterAttachments"):
                    revision[key] = [
                        self.app.platform.discussion_attachments.public_reference(
                            c, result["questionId"], item["id"]
                        )
                        for item in revision.get(key, [])
                    ]
        return result
