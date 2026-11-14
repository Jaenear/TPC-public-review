"""Private Q/A bytes with explicit, server-injected policy and inspection.

No policy/scanner is enabled by default. Fixture approval is not operating policy.
Small bounded bodies live atomically in the member DB, never in public static or
temporary filesystem paths. A disconnected upload creates no partial record.
"""

from dataclasses import dataclass
import hashlib
import json
import re
import socket
import time
from urllib.parse import quote, unquote
from ..core import ApiError, new_id, now_iso, text_field
from ..discussion_records import canonical, digest
from .common import audit, can_manage_course
from .discussion_api import request_key, actor_role

FORMATS = {".txt": "text/plain", ".json": "application/json"}
HARD_MAX_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class AttachmentProfile:
    reference: str
    max_bytes: int
    max_total_bytes: int
    max_pending: int
    pending_seconds: int
    suffixes: tuple

    def identity(self):
        return digest(self.__dict__)

    def __post_init__(self):
        if (
            not isinstance(self.reference, str)
            or not 5 <= len(self.reference) <= 200
            or any(ord(ch) < 32 for ch in self.reference)
            or any(
                type(v) is not int
                for v in (
                    self.max_bytes,
                    self.max_total_bytes,
                    self.max_pending,
                    self.pending_seconds,
                )
            )
            or not 1 <= self.max_bytes <= HARD_MAX_BYTES
            or not self.max_bytes <= self.max_total_bytes <= 1024 * HARD_MAX_BYTES
            or not 1 <= self.max_pending <= 30
            or not 60 <= self.pending_seconds <= 7 * 86400
            or not self.suffixes
            or len(set(self.suffixes)) != len(self.suffixes)
            or not set(self.suffixes) <= set(FORMATS)
        ):
            raise ValueError("Explicit supported attachment policy is required")


def validate_body(body, suffix):
    try:
        text = body.decode("utf-8-sig")
        if not text.strip() or any(ord(ch) < 32 and ch not in "\t\r\n" for ch in text):
            raise ValueError("Not plain UTF-8")
        if suffix == ".json":

            def invalid_constant(_):
                raise ValueError("Non-finite JSON")

            if not isinstance(
                json.loads(text, parse_constant=invalid_constant), (dict, list)
            ):
                raise ValueError("JSON object or array required")
        elif text.lstrip().startswith(("<", "{", "[", "%PDF-", "#!")):
            raise ValueError(
                "Structured/binary/executable content is not this text profile"
            )
    except (UnicodeError, ValueError, RecursionError):
        raise ApiError(
            400, "파일 확장자와 실제 UTF-8 텍스트/JSON 형식이 일치하는지 확인해주세요."
        )


class DiscussionAttachments:
    def __init__(self, app, discussions):
        self.app = app
        self.history = discussions.history
        self.profile = None
        self.scanner = None
        self.clock = time.time

    def configured(self):
        return isinstance(self.profile, AttachmentProfile) and callable(
            getattr(self.scanner, "inspect", None)
        )

    def policy(self):
        if not self.configured():
            raise ApiError(
                409,
                "첨부 운영 기준(DEC-09)과 검사 연결을 준비 중입니다. 본문 수정은 이용할 수 있습니다.",
            )
        return self.profile

    def question_scope(self, c, user, qid, writing=False):
        user, _, question, head = self.history.scope(c, user, "question", qid)
        course = self.app.platform.discussions.course(c, question["course_id"])
        if not writing and head["locked"] and not can_manage_course(c, user, course):
            raise ApiError(404, "현재 열람할 수 있는 첨부를 찾을 수 없습니다.")
        if writing and (
            head["hidden"]
            or head["locked"]
            or question["status"] == "closed"
            or course["status"] != "published"
        ):
            raise ApiError(409, "운영 처리된 질문의 첨부는 변경할 수 없습니다.")
        return user, question, course

    def asset_scope(self, c, user, aid, writing=False):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        row = c.execute(
            "SELECT a.*,s.status,s.version FROM question_attachments a JOIN question_attachment_states s ON s.attachment_id=a.id WHERE a.id=?",
            (aid,),
        ).fetchone()
        if not row:
            raise ApiError(404, "첨부를 찾을 수 없습니다.")
        user, question, course = self.question_scope(
            c, user, row["question_id"], writing
        )
        return user, question, course, row

    def current_reference(self, c, qid, aid):
        if not c.execute(
            "SELECT 1 FROM question_content_heads WHERE target_id=? AND hidden=0 AND locked=0",
            (qid,),
        ).fetchone():
            return False
        return bool(
            c.execute(
                "SELECT 1 FROM question_content_heads h JOIN question_revisions r ON r.id=h.revision_id,json_each(r.after_snapshot,'$.attachments') j WHERE h.target_id=? AND h.hidden=0 AND h.locked=0 AND j.value=? UNION ALL SELECT 1 FROM answer_content_heads h JOIN answer_revisions r ON r.id=h.revision_id,json_each(r.after_snapshot,'$.attachments') j WHERE h.question_id=? AND h.hidden=0 AND h.locked=0 AND j.value=? LIMIT 1",
                (qid, aid, qid, aid),
            ).fetchone()
        )

    def public_reference(self, c, qid, aid):
        row = c.execute(
            "SELECT a.id,a.filename,a.media_type,a.size,s.status FROM question_attachments a JOIN question_attachment_states s ON s.attachment_id=a.id JOIN question_attachment_payloads p ON p.attachment_id=a.id WHERE a.id=? AND a.question_id=?",
            (aid, qid),
        ).fetchone()
        if (
            not row
            or row["status"] != "approved"
            or not self.current_reference(c, qid, aid)
        ):
            return {"id": aid, "available": False}
        return {
            "id": aid,
            "available": True,
            "filename": row["filename"],
            "mediaType": row["media_type"],
            "size": row["size"],
            "href": "/api/qa/attachments/" + aid + "/file",
        }

    def listing(self, user, qid):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user, question, course = self.question_scope(c, user, qid)
            head = c.execute(
                "SELECT hidden,locked FROM question_content_heads WHERE target_id=?",
                (qid,),
            ).fetchone()
            writing = not (
                head["hidden"]
                or head["locked"]
                or question["status"] == "closed"
                or course["status"] != "published"
            )
            ready = self.configured() and writing
            items = []
            for row in c.execute(
                "SELECT a.*,s.status,s.version FROM question_attachments a JOIN question_attachment_states s ON s.attachment_id=a.id WHERE a.question_id=? ORDER BY a.created_at,a.id",
                (qid,),
            ):
                item = self.public_reference(c, qid, row["id"])
                if row["uploader_id"] == user["id"] or can_manage_course(
                    c, user, course
                ):
                    item.update(status=row["status"], version=row["version"])
                    own, manager = row["uploader_id"] == user["id"], can_manage_course(
                        c, user, course
                    )
                    payload = c.execute(
                        "SELECT expires_at,policy_reference FROM question_attachment_payloads WHERE attachment_id=?",
                        (row["id"],),
                    ).fetchone()
                    item.update(
                        canReference=bool(
                            writing and own and row["status"] == "approved" and payload
                        ),
                        canInspect=bool(
                            ready
                            and row["status"] == "pending"
                            and payload
                            and payload["expires_at"] > int(self.clock())
                            and payload["policy_reference"] == self.profile.identity()
                        ),
                        canCancel=bool(
                            writing and own and row["status"] in ("pending", "rejected")
                        ),
                        canBlock=bool(
                            manager and row["status"] in ("pending", "approved")
                        ),
                        canDelete=bool(
                            user["role"] == "admin" and row["status"] != "deleted"
                        ),
                    )
                    if row["status"] not in ("blocked", "deleted"):
                        item.update(
                            filename=row["filename"],
                            size=row["size"],
                            mediaType=row["media_type"],
                        )
                    items.append(item)
                elif item["available"]:
                    items.append(item)
            return {
                "questionId": qid,
                "attachments": items,
                "upload": {
                    "ready": ready,
                    "decisionId": "DEC-09",
                    "maxBytes": self.profile.max_bytes if ready else None,
                    "accept": list(self.profile.suffixes) if ready else [],
                    "message": (
                        "검사 대기 자료는 본문에 연결하거나 내려받을 수 없습니다."
                        if ready
                        else "첨부 운영 기준과 검사 연결을 준비 중입니다. 본문 수정·이력은 이용할 수 있습니다."
                    ),
                },
            }

    def receipt(self, c, user, key, hashed):
        row = c.execute(
            "SELECT * FROM question_attachment_operations WHERE actor_id=? AND request_key=?",
            (user["id"], key),
        ).fetchone()
        if row and row["request_hash"] != hashed:
            raise ApiError(409, "같은 요청 식별자로 다른 첨부 작업을 할 수 없습니다.")
        return json.loads(row["result"]) if row else None

    def response(self, c, result):
        row = c.execute(
            "SELECT status,version FROM question_attachment_states WHERE attachment_id=?",
            (result["id"],),
        ).fetchone()
        return {
            "attachment": result,
            "currentAttachment": {
                "id": result["id"],
                "status": row["status"],
                "version": row["version"],
            },
        }

    def record(
        self, c, user, course, row, action, expected, status, key, hashed, reason
    ):
        result = {
            "id": row["id"],
            "questionId": row["question_id"],
            "version": expected + 1,
            "status": status,
            "requestKey": key,
        }
        c.execute(
            "INSERT INTO question_attachment_operations(id,attachment_id,question_id,actor_id,actor_role,action,expected_version,result_version,result_status,request_key,request_hash,reason,result,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id("qaattachmentop"),
                row["id"],
                row["question_id"],
                user["id"] if user else None,
                actor_role(c, user, course) if user else "system",
                action,
                expected,
                expected + 1,
                status,
                key,
                hashed,
                reason,
                canonical(result),
                now_iso(),
            ),
        )
        audit(
            c,
            user,
            "qa.attachment_" + action,
            "question_attachment",
            row["id"],
            {
                "questionId": row["question_id"],
                "version": expected + 1,
                "status": status,
            },
        )
        return result

    def cleanup_expired(self, c, uid):
        rows = c.execute(
            "SELECT a.id,a.question_id,s.version FROM question_attachments a JOIN question_attachment_states s ON s.attachment_id=a.id JOIN question_attachment_payloads p ON p.attachment_id=a.id WHERE a.uploader_id=? AND s.status='pending' AND p.expires_at<=? LIMIT 30",
            (uid, int(self.clock())),
        ).fetchall()
        for row in rows:
            c.execute(
                "UPDATE question_attachment_states SET status='deleted',version=version+1,reason='임시 보관 기한 만료',updated_at=? WHERE attachment_id=?",
                (now_iso(), row["id"]),
            )
            c.execute(
                "DELETE FROM question_attachment_payloads WHERE attachment_id=?",
                (row["id"],),
            )
            self.record(
                c,
                None,
                None,
                row,
                "expired",
                row["version"],
                "deleted",
                "expired-" + row["id"],
                digest({"id": row["id"], "action": "expired"}),
                "임시 보관 기한 만료",
            )

    def upload(self, h, user, qid):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        self.app.limiter.check(("qa-attachment", user["id"]), 30, 3600)
        with self.app.db.connect() as c:
            self.question_scope(c, user, qid, True)
        profile = self.policy()
        if any(
            len(h.headers.get_all(name, [])) != 1
            for name in (
                "Content-Length",
                "Content-Type",
                "X-File-Name",
                "X-Request-Key",
            )
        ):
            raise ApiError(400, "첨부 헤더를 한 번씩 명확히 전송해주세요.")
        key = request_key(h.headers.get("X-Request-Key"))
        if h.headers.get("Transfer-Encoding"):
            raise ApiError(400, "정확한 첨부 크기가 필요합니다.")
        try:
            length = int(h.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(400, "첨부 크기를 확인해주세요.")
        if not 1 <= length <= profile.max_bytes:
            raise ApiError(413, "첨부 파일의 빈 내용 또는 허용 크기를 확인해주세요.")
        encoded = h.headers.get("X-File-Name", "")
        try:
            if len(encoded) > 1200 or re.search(r"%(?![0-9A-Fa-f]{2})", encoded):
                raise ValueError()
            filename = unquote(encoded, errors="strict")
        except (ValueError, UnicodeError):
            raise ApiError(400, "첨부 파일명을 확인해주세요.")
        if (
            not filename
            or len(filename) > 255
            or filename != filename.strip()
            or any(ch in filename for ch in "/\\")
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in filename)
        ):
            raise ApiError(400, "첨부 파일명에 경로/제어 문자를 사용할 수 없습니다.")
        suffix = "." + filename.rsplit(".", 1)[-1].lower()
        mime = h.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if suffix not in profile.suffixes or mime != FORMATS[suffix]:
            raise ApiError(415, "운영 기준에서 허용한 파일 형식을 선택해주세요.")
        h.connection.settimeout(15)
        try:
            body = h.rfile.read(length)
        except (OSError, socket.timeout):
            raise ApiError(400, "파일 전송이 중단되었습니다. 원본을 다시 선택해주세요.")
        if len(body) != length:
            raise ApiError(400, "파일 전송이 끝나지 않았습니다. 다시 선택해주세요.")
        validate_body(body, suffix)
        sha = hashlib.sha256(body).hexdigest()
        hashed = digest(
            {
                "questionId": qid,
                "filename": filename,
                "size": length,
                "sha256": sha,
                "mediaType": mime,
                "policy": profile.identity(),
            }
        )
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, _, course = self.question_scope(c, user, qid, True)
            if self.policy() != profile:
                raise ApiError(
                    409, "첨부 운영 기준이 변경되었습니다. 다시 확인해주세요."
                )
            replay = self.receipt(c, user, key, hashed)
            if replay:
                return self.response(c, replay)
            self.cleanup_expired(c, user["id"])
            quota = c.execute(
                "SELECT COUNT(CASE WHEN s.status='pending' THEN 1 END),COALESCE(SUM(p.size),0) FROM question_attachments a JOIN question_attachment_states s ON s.attachment_id=a.id JOIN question_attachment_payloads p ON p.attachment_id=a.id WHERE a.uploader_id=?",
                (user["id"],),
            ).fetchone()
            if (
                quota[0] >= profile.max_pending
                or quota[1] + length > profile.max_total_bytes
            ):
                raise ApiError(
                    409,
                    "첨부 보관 한도입니다. 대기 자료를 정리한 뒤 다시 시도해주세요.",
                )
            aid, timestamp = new_id("qaattachment"), now_iso()
            c.execute(
                "INSERT INTO question_attachments(id,question_id,uploader_id,filename,storage_key,media_type,size,sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    aid,
                    qid,
                    user["id"],
                    filename,
                    new_id("qastorage"),
                    mime,
                    length,
                    sha,
                    timestamp,
                ),
            )
            c.execute(
                "INSERT INTO question_attachment_states(attachment_id,updated_at) VALUES(?,?)",
                (aid, timestamp),
            )
            c.execute(
                "INSERT INTO question_attachment_payloads(attachment_id,question_id,body,size,sha256,policy_reference,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    aid,
                    qid,
                    body,
                    length,
                    sha,
                    profile.identity(),
                    int(self.clock()) + profile.pending_seconds,
                    timestamp,
                ),
            )
            row = c.execute(
                "SELECT * FROM question_attachments WHERE id=?", (aid,)
            ).fetchone()
            result = self.record(
                c,
                user,
                course,
                row,
                "upload",
                0,
                "pending",
                key,
                hashed,
                "검사 대기 원본 수신",
            )
            return self.response(c, result)

    def operate(self, user, aid, action, data):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        self.app.limiter.check(("qa-attachment-op", user["id"]), 60, 3600)
        allowed = {"requestKey", "expectedVersion"} | (
            {"reason"} if action in ("block", "delete") else set()
        )
        if set(data) - allowed or action not in (
            "inspect",
            "cancel",
            "block",
            "delete",
        ):
            raise ApiError(400, "첨부 처리 항목을 확인해주세요.")
        key = request_key(data.get("requestKey"))
        expected = data.get("expectedVersion")
        if type(expected) is not int or expected < 1:
            raise ApiError(400, "첨부 기준 버전을 확인해주세요.")
        reason = (
            text_field(data, "reason", minimum=5, maximum=2000)
            if action in ("block", "delete")
            else "검사 요청" if action == "inspect" else "업로더의 임시 자료 취소"
        )
        hashed = digest(
            {"id": aid, "action": action, "expectedVersion": expected, "reason": reason}
        )
        with self.app.db.connect() as c:
            user, _, course, row = self.asset_scope(
                c, user, aid, action in ("inspect", "cancel")
            )
            self.operator(c, user, course, row, action)
        scan = None
        if action == "inspect":
            profile = self.policy()
            with self.app.db.connect() as c:
                c.execute("BEGIN")
                user, _, course, row = self.asset_scope(c, user, aid, True)
                self.operator(c, user, course, row, action)
                replay = self.receipt(c, user, key, hashed)
                if replay:
                    return self.response(c, replay)
                payload = c.execute(
                    "SELECT * FROM question_attachment_payloads WHERE attachment_id=?",
                    (aid,),
                ).fetchone()
                if (
                    row["version"] != expected
                    or row["status"] != "pending"
                    or not payload
                    or payload["expires_at"] <= int(self.clock())
                    or payload["policy_reference"] != profile.identity()
                ):
                    raise ApiError(
                        409, "현재 검사할 수 있는 원본과 버전을 확인해주세요."
                    )
                body = payload["body"]
                if (
                    len(body) != row["size"]
                    or hashlib.sha256(body).hexdigest() != row["sha256"]
                ):
                    raise ApiError(409, "검사 원본의 무결성을 확인할 수 없습니다.")
            try:
                scan = self.scanner.inspect(
                    body,
                    {
                        "filename": row["filename"],
                        "mediaType": row["media_type"],
                        "sha256": row["sha256"],
                        "policyReference": profile.reference,
                    },
                )
            except Exception:
                raise ApiError(
                    503,
                    "첨부 검사를 완료하지 못했습니다. 대기 상태에서 다시 확인해주세요.",
                )
            if (
                not isinstance(scan, dict)
                or scan.get("status") not in ("approved", "rejected")
                or not isinstance(scan.get("reference"), str)
                or not 5 <= len(scan["reference"]) <= 200
                or any(ord(ch) < 32 for ch in scan["reference"])
            ):
                raise ApiError(
                    503, "첨부 검사 결과를 확인할 수 없습니다. 대기 상태를 유지합니다."
                )
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, _, course, row = self.asset_scope(
                c, user, aid, action in ("inspect", "cancel")
            )
            self.operator(c, user, course, row, action)
            replay = self.receipt(c, user, key, hashed)
            if replay:
                return self.response(c, replay)
            if row["version"] != expected:
                raise ApiError(
                    409, "다른 첨부 변경이 있습니다. 현재 상태를 확인해주세요."
                )
            if action == "inspect":
                payload = c.execute(
                    "SELECT * FROM question_attachment_payloads WHERE attachment_id=?",
                    (aid,),
                ).fetchone()
                if (
                    self.policy() != profile
                    or row["status"] != "pending"
                    or not payload
                    or payload["expires_at"] <= int(self.clock())
                ):
                    raise ApiError(409, "검사 중 첨부 상태/운영 기준이 변경되었습니다.")
                if (
                    len(payload["body"]) != row["size"]
                    or hashlib.sha256(payload["body"]).hexdigest() != row["sha256"]
                ):
                    raise ApiError(409, "검사 중 원본 무결성이 변경되었습니다.")
                status, reference = scan["status"], scan["reference"]
                reason = "등록된 검사 어댑터의 결과: " + status
            else:
                status, reference = ("blocked" if action == "block" else "deleted"), ""
                allowed_status = (
                    ("pending", "rejected")
                    if action == "cancel"
                    else (
                        ("pending", "approved")
                        if action == "block"
                        else ("pending", "approved", "rejected", "blocked")
                    )
                )
                if row["status"] not in allowed_status:
                    raise ApiError(409, "현재 첨부 상태에서 이 처리를 할 수 없습니다.")
            c.execute(
                "UPDATE question_attachment_states SET status=?,version=version+1,inspection_reference=CASE WHEN ?='' THEN inspection_reference ELSE ? END,reason=?,updated_at=? WHERE attachment_id=?",
                (status, reference, reference, reason, now_iso(), aid),
            )
            if status in ("deleted", "rejected"):
                c.execute(
                    "DELETE FROM question_attachment_payloads WHERE attachment_id=?",
                    (aid,),
                )
            return self.response(
                c,
                self.record(
                    c, user, course, row, action, expected, status, key, hashed, reason
                ),
            )

    def operator(self, c, user, course, row, action):
        own = row["uploader_id"] == user["id"]
        manager = can_manage_course(c, user, course)
        if (
            (action == "cancel" and not own)
            or (
                action in ("inspect", "block")
                and not (manager or (action == "inspect" and own))
            )
            or (action == "delete" and user["role"] != "admin")
        ):
            raise ApiError(403, "이 첨부 작업을 처리할 권한이 없습니다.")

    def file(self, h, user, aid):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            _, question, _, row = self.asset_scope(c, user, aid)
            ref = self.public_reference(c, question["id"], aid)
            if not ref["available"]:
                raise ApiError(404, "현재 이용할 수 있는 첨부를 찾을 수 없습니다.")
            payload = c.execute(
                "SELECT body FROM question_attachment_payloads WHERE attachment_id=?",
                (aid,),
            ).fetchone()
            body = payload["body"]
            if (
                len(body) != row["size"]
                or hashlib.sha256(body).hexdigest() != row["sha256"]
            ):
                raise ApiError(409, "첨부 원본의 무결성을 확인할 수 없습니다.")
        h.send_response(200)
        h.send_header("Content-Type", row["media_type"])
        h.send_header("Content-Length", str(len(body)))
        h.send_header(
            "Content-Disposition",
            "attachment; filename=\"tpc-attachment\"; filename*=UTF-8''"
            + quote(row["filename"], safe=""),
        )
        h.send_header("Cache-Control", "private, no-store")
        h.security_headers()
        h.end_headers()
        if h.command != "HEAD":
            h.wfile.write(body)
