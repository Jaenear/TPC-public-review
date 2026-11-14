"""Transactional Q/A content model. Public edit/upload routes are a later slice.

Attachment identities contain no public URL or bytes. Only a future inspected
storage adapter may approve/serve a file; imported metadata alone grants none.
"""

import json
import re
from ..core import ApiError, text_field, new_id, now_iso
from ..discussion_records import canonical, content, digest, KINDS
from .common import can_manage_course, audit
from .learning_start import active_member


class DiscussionHistory:
    def __init__(self, discussions):
        self.discussions = discussions
        self.app = discussions.app

    def scope(self, c, user, kind, target_id, editing=False, history_read=False):
        if kind not in KINDS:
            raise ApiError(404, "내용을 찾을 수 없습니다.")
        user = active_member(c, user)
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        row = c.execute(
            f"SELECT * FROM {KINDS[kind]} WHERE id=?", (target_id,)
        ).fetchone()
        if not row:
            raise ApiError(404, "내용을 찾을 수 없습니다.")
        qid = target_id if kind == "question" else row["question_id"]
        question, course = self.discussions.question(c, user, qid)
        head = c.execute(
            f"SELECT * FROM {kind}_content_heads WHERE target_id=?", (target_id,)
        ).fetchone()
        if not head:
            raise ApiError(409, "현재 본문 기준본을 확인해주세요.")
        manager = can_manage_course(c, user, course)
        if head["hidden"] and not manager:
            raise ApiError(404, "내용을 찾을 수 없습니다.")
        qhead = c.execute(
            "SELECT hidden,locked FROM question_content_heads WHERE target_id=?", (qid,)
        ).fetchone()
        if history_read and (head["locked"] or qhead["locked"]) and not manager:
            raise ApiError(404, "현재 열람할 수 있는 수정 이력을 찾을 수 없습니다.")
        if editing:
            if user["id"] != row["user_id"] and user["role"] != "admin":
                raise ApiError(403, "작성한 내용만 수정할 수 있습니다.")
            if kind == "answer" and row["official"] and not manager:
                raise ApiError(403, "현재 담당 코치 자격으로 공식 답변을 수정해주세요.")
            if (
                question["status"] == "closed"
                or course["status"] != "published"
                or head["hidden"]
                or head["locked"]
                or qhead["hidden"]
                or qhead["locked"]
            ):
                raise ApiError(409, "닫히거나 운영 처리된 내용은 수정할 수 없습니다.")
        return user, row, question, head

    def read(self, user, kind, target_id, before=None, limit=100):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            _, _, question, head = self.scope(
                c, user, kind, target_id, history_read=True
            )
            records = []
            rows = c.execute(
                f"SELECT * FROM {kind}_revisions WHERE target_id=? AND version<? ORDER BY version DESC LIMIT ?",
                (target_id, before or head["version"] + 1, limit + 1),
            ).fetchall()
            for row in rows[:limit]:
                item = {
                    "id": row["id"],
                    "version": row["version"],
                    "expectedVersion": row["expected_version"],
                    "origin": row["origin"],
                    "actorId": row["actor_id"],
                    "reason": row["reason"],
                    "recordedAt": row["recorded_at"],
                    "beforeHash": row["before_sha256"],
                    "afterHash": row["after_sha256"],
                }
                for source, key in (
                    ("before_snapshot", "before"),
                    ("after_snapshot", "after"),
                ):
                    snapshot = json.loads(row[source]) if row[source] else None
                    if snapshot is not None:
                        item[key + "Attachments"] = [
                            self.attachment(c, question["id"], aid)
                            for aid in snapshot["attachments"]
                        ]
                    item[key] = snapshot
                records.append(item)
            return {
                "targetId": target_id,
                "questionId": question["id"],
                "contentVersion": head["version"],
                "revisions": records,
                "hasMore": len(rows) > limit,
                "nextBeforeVersion": (
                    records[-1]["version"] if len(rows) > limit else None
                ),
            }

    def attachment(self, c, question_id, aid):
        row = c.execute(
            "SELECT a.id,a.filename,a.size,a.media_type,s.status FROM question_attachments a JOIN question_attachment_states s ON s.attachment_id=a.id WHERE a.id=? AND a.question_id=?",
            (aid, question_id),
        ).fetchone()
        if not row or row["status"] != "approved":
            return {"id": aid, "available": False}
        # No storage key, checksum, uploader details or download URL in history.
        return {
            "id": aid,
            "available": True,
            "filename": row["filename"],
            "size": row["size"],
            "mediaType": row["media_type"],
        }

    def stage_attachment(self, user, question_id, data):
        """Internal metadata reservation, not an upload/inspection acceptance."""
        filename = text_field(data, "filename", maximum=255)
        media_type = text_field(data, "mediaType", maximum=100)
        sha, size = data.get("sha256"), data.get("size")
        if (
            filename in (".", "..")
            or any(ch in filename for ch in "/\\")
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in filename)
            or not re.fullmatch(r"[a-f0-9]{64}", sha if isinstance(sha, str) else "")
            or type(size) is not int
            or not 1 <= size <= 2147483647
        ):
            raise ApiError(400, "첨부 식별 정보를 확인해주세요.")
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, _, question, head = self.scope(c, user, "question", question_id)
            course = self.discussions.course(c, question["course_id"])
            if (
                head["hidden"]
                or head["locked"]
                or question["status"] == "closed"
                or course["status"] != "published"
            ):
                raise ApiError(409, "운영 처리된 질문에 첨부를 준비할 수 없습니다.")
            aid, timestamp = new_id("qaattachment"), now_iso()
            c.execute(
                "INSERT INTO question_attachments(id,question_id,uploader_id,filename,storage_key,media_type,size,sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    aid,
                    question_id,
                    user["id"],
                    filename,
                    new_id("qastorage"),
                    media_type,
                    size,
                    sha,
                    timestamp,
                ),
            )
            c.execute(
                "INSERT INTO question_attachment_states(attachment_id,updated_at) VALUES(?,?)",
                (aid, timestamp),
            )
            return {"id": aid, "status": "pending"}

    def prepare(self, kind, data):
        reason = text_field(data, "reason", minimum=5, maximum=2000)
        body = text_field(
            data, "body", minimum=10 if kind == "question" else 2, maximum=10000
        )
        title = (
            text_field(data, "title", minimum=3, maximum=150)
            if kind == "question"
            else None
        )
        expected = data.get("expectedVersion")
        changes = data.get("attachments", {"add": [], "detach": []})
        if (
            type(expected) is not int
            or expected < 1
            or not isinstance(changes, dict)
            or set(changes) != {"add", "detach"}
        ):
            raise ApiError(400, "본문 기준 버전과 첨부 변경 명세를 확인해주세요.")
        for ids in changes.values():
            if (
                not isinstance(ids, list)
                or len(ids) > 30
                or any(
                    not isinstance(aid, str)
                    or not re.fullmatch(r"qaattachment_[a-f0-9]{32}", aid)
                    for aid in ids
                )
                or len(set(ids)) != len(ids)
            ):
                raise ApiError(400, "첨부 변경 명세를 확인해주세요.")
        if set(changes["add"]) & set(changes["detach"]):
            raise ApiError(400, "같은 첨부를 추가하고 분리할 수 없습니다.")
        return {
            "expectedVersion": expected,
            "body": body,
            **({"title": title} if kind == "question" else {}),
            "reason": reason,
            "attachments": {k: sorted(v) for k, v in changes.items()},
        }

    def edit(self, user, kind, target_id, data):
        prepared = self.prepare(kind, data)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            return self.edit_in(c, user, kind, target_id, prepared)

    def edit_in(self, c, user, kind, target_id, prepared):
        reason, body = prepared["reason"], prepared["body"]
        title, expected = prepared.get("title"), prepared["expectedVersion"]
        changes = prepared["attachments"]
        user, row, question, head = self.scope(c, user, kind, target_id, editing=True)
        if expected != head["version"]:
            raise ApiError(409, "다른 본문 변경이 있습니다. 현재 원문을 확인해주세요.")
        previous = c.execute(
            f"SELECT * FROM {kind}_revisions WHERE id=?", (head["revision_id"],)
        ).fetchone()
        before = json.loads(previous["after_snapshot"])
        if (
            digest(content(kind, row, before["attachments"]))
            != previous["after_sha256"]
        ):
            raise ApiError(
                409, "원문과 기준본이 일치하지 않습니다. 운영 확인이 필요합니다."
            )
        attachments = set(before["attachments"])
        if (
            not set(changes["detach"]) <= attachments
            or set(changes["add"]) & attachments
        ):
            raise ApiError(409, "현재 첨부 구성이 변경되었습니다.")
        for aid in changes["add"]:
            asset = c.execute(
                "SELECT a.uploader_id,s.status FROM question_attachments a JOIN question_attachment_states s ON s.attachment_id=a.id WHERE a.id=? AND a.question_id=?",
                (aid, question["id"]),
            ).fetchone()
            if (
                not asset
                or asset["status"] != "approved"
                or asset["uploader_id"] != user["id"]
            ):
                raise ApiError(409, "검사를 통과한 본인 첨부만 추가할 수 있습니다.")
        attachments.difference_update(changes["detach"])
        attachments.update(changes["add"])
        after = {**before, "body": body, "attachments": sorted(attachments)}
        if kind == "question":
            after["title"] = title
        if after == before:
            raise ApiError(400, "수정한 내용을 확인해주세요.")
        rid, timestamp = new_id("qarevision"), now_iso()
        c.execute(
            f"INSERT INTO {kind}_revisions(id,target_id,question_id,version,expected_version,origin,actor_id,reason,before_snapshot,after_snapshot,before_sha256,after_sha256,attachment_changes,recorded_at) VALUES(?,?,?,?,?,'edited',?,?,?,?,?,?,?,?)",
            (
                rid,
                target_id,
                question["id"],
                expected + 1,
                expected,
                user["id"],
                reason,
                canonical(before),
                canonical(after),
                digest(before),
                digest(after),
                canonical(changes),
                timestamp,
            ),
        )
        updated = c.execute(
            f"UPDATE {kind}_content_heads SET revision_id=?,version=version+1 WHERE target_id=? AND version=?",
            (rid, target_id, expected),
        )
        if updated.rowcount != 1:
            raise ApiError(409, "다른 본문 변경이 있습니다.")
        if kind == "question":
            c.execute(
                "UPDATE questions SET title=?,body=?,version=version+1,updated_at=? WHERE id=?",
                (title, body, timestamp, target_id),
            )
        else:
            c.execute("UPDATE answers SET body=? WHERE id=?", (body, target_id))
            c.execute(
                "UPDATE questions SET version=version+1,updated_at=? WHERE id=?",
                (timestamp, question["id"]),
            )
        audit(
            c,
            user,
            "qa.content_edited",
            kind,
            target_id,
            {
                "revisionId": rid,
                "contentVersion": expected + 1,
                "questionId": question["id"],
            },
        )
        return {
            "id": target_id,
            "questionId": question["id"],
            "contentVersion": expected + 1,
            "revisionId": rid,
        }
