"""Private, immutable lesson files with explicit operator review and access checks.

Identification checks are deliberately not a PDF parser or malware scanner.
Files are attachment downloads, never executable/inline course content.
"""

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import quote, unquote
from ..core import ApiError, now_iso, new_id, text_field, choice_field
from .common import audit, can_manage_course, has_access, page_args, version
from .pricing import managed

MAX_BYTES = 10 * 1024 * 1024
FILE_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".json": "application/json",
}


def validate_bytes(body, suffix):
    if suffix == ".pdf":
        if not re.match(
            rb"%PDF-[12]\.\d[\r\n]", body[:16]
        ) or not body.rstrip().endswith(b"%%EOF"):
            raise ApiError(
                400, "PDF 시작/종료 표식을 확인해주세요. 파일을 다시 내보내주세요."
            )
        return
    try:
        value = body.decode("utf-8-sig")
        if any(ord(ch) < 32 and ch not in "\t\r\n" for ch in value):
            raise ValueError("Control character")
        if suffix == ".json" and not isinstance(json.loads(value), (list, dict)):
            raise ValueError("JSON must be an object or array")
    except (ValueError, UnicodeError, RecursionError):
        raise ApiError(400, "UTF-8 텍스트 또는 객체/배열 형식의 JSON을 확인해주세요.")


def normalize_refs(c, course_id, data, published=False):
    if not isinstance(data, list) or len(data) > 20:
        raise ApiError(400, "차시별 학습 자료는 20개 이내로 연결해주세요.")
    refs, seen = [], set()
    for item in data:
        if not isinstance(item, dict):
            raise ApiError(400, "학습 자료 연결 형식을 확인해주세요.")
        rid = text_field(item, "assetId", maximum=100)
        if rid in seen:
            raise ApiError(400, "같은 학습 자료를 중복 연결할 수 없습니다.")
        row = c.execute(
            "SELECT * FROM learning_resources WHERE id=? AND course_id=?",
            (rid, course_id),
        ).fetchone()
        if not row or row["status"] in ("rejected", "withdrawn"):
            raise ApiError(409, "이 강의의 사용 가능한 학습 자료를 선택해주세요.")
        if published and row["status"] != "approved":
            raise ApiError(
                409, "공개 차시에는 관리자가 검수한 학습 자료만 연결할 수 있습니다."
            )
        refs.append(
            {"assetId": rid, "label": text_field(item, "label", minimum=2, maximum=120)}
        )
        seen.add(rid)
    return refs


def learner_resources(c, course_id, refs):
    result = []
    for ref in refs:
        row = c.execute(
            "SELECT id,filename,mime_type,size,status FROM learning_resources WHERE id=? AND course_id=?",
            (ref["assetId"], course_id),
        ).fetchone()
        result.append(
            {
                **ref,
                "filename": row["filename"] if row else "",
                "mimeType": row["mime_type"] if row else "",
                "size": row["size"] if row else 0,
                "available": bool(row and row["status"] == "approved"),
            }
        )
    return result


class ResourceService:
    def __init__(self, app):
        self.app = app
        self.directory = Path(app.db.path).parent / (
            Path(app.db.path).stem + "-resources"
        )
        self.directory.mkdir(exist_ok=True, mode=0o700)

    def event(self, c, user, rid, action, reason):
        c.execute(
            "INSERT INTO resource_events VALUES(?,?,?,?,?,?)",
            (new_id("resourceevent"), rid, user["id"], action, reason, now_iso()),
        )
        audit(c, user, "resource." + action, "resource", rid)

    def view(self, row, admin=False):
        value = {
            "id": row["id"],
            "courseId": row["course_id"],
            "uploaderId": row["uploader_id"],
            "filename": row["filename"],
            "mimeType": row["mime_type"],
            "size": row["size"],
            "sha256": row["sha256"],
            "status": row["status"],
            "version": row["version"],
            "reviewNote": row["review_note"],
            "createdAt": row["created_at"],
            "reviewedAt": row["reviewed_at"],
            "scanStatus": "operator_review",
        }
        if admin:
            value["approvalReference"] = row["approval_reference"]
        return value

    def listing(self, user, cid, query):
        page, size, offset = page_args(query)
        with self.app.db.connect() as c:
            user, _ = managed(c, user, cid)
            where, args = " WHERE course_id=?", [cid]
            state = query.get("status", [""])[0]
            if state:
                if state not in ("pending", "approved", "rejected", "withdrawn"):
                    raise ApiError(400, "자료 상태를 확인해주세요.")
                where += " AND status=?"
                args.append(state)
            rows = c.execute(
                "SELECT * FROM learning_resources"
                + where
                + " ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?",
                args + [size, offset],
            ).fetchall()
            return {
                "resources": [self.view(r, user["role"] == "admin") for r in rows],
                "page": page,
                "pageSize": size,
                "total": c.execute(
                    "SELECT COUNT(*) FROM learning_resources" + where, args
                ).fetchone()[0],
                "limits": {"maxBytes": MAX_BYTES, "types": FILE_TYPES, "perLesson": 20},
            }

    def exact(self, user, cid, rid):
        with self.app.db.connect() as c:
            user, _ = managed(c, user, cid)
            row = c.execute(
                "SELECT * FROM learning_resources WHERE id=? AND course_id=?",
                (rid, cid),
            ).fetchone()
            if not row:
                raise ApiError(404, "학습 자료를 찾을 수 없습니다.")
            return {
                "resource": self.view(row, user["role"] == "admin"),
                "events": [
                    dict(r)
                    for r in c.execute(
                        "SELECT action,reason,created_at FROM resource_events WHERE resource_id=? ORDER BY rowid",
                        (rid,),
                    )
                ],
            }

    def upload(self, h, user, cid):
        with self.app.db.connect() as c:
            user, _ = managed(c, user, cid)
            self.check_capacity(c, cid)
        try:
            size = int(h.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(400, "자료 크기를 확인해주세요.")
        if not 1 <= size <= MAX_BYTES:
            raise ApiError(413, "학습 자료는 파일별 10MB 이내로 업로드해주세요.")
        name = Path(unquote(h.headers.get("X-File-Name", ""))).name
        suffix = Path(name).suffix.lower()
        mime = h.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if (
            not name
            or len(name) > 150
            or suffix not in FILE_TYPES
            or mime != FILE_TYPES[suffix]
            or any(ord(ch) < 32 for ch in name)
        ):
            raise ApiError(
                415, "PDF·UTF-8 TXT·JSON 파일과 올바른 파일 형식을 확인해주세요."
            )
        self.app.limiter.check(("resource", user["id"]), 30, 3600)
        h.connection.settimeout(30)
        remaining, chunks = size, []
        while remaining:
            part = h.rfile.read(min(65536, remaining))
            if not part:
                raise ApiError(400, "자료 업로드가 완료되지 않았습니다.")
            chunks.append(part)
            remaining -= len(part)
        body = b"".join(chunks)
        validate_bytes(body, suffix)
        rid = new_id("resource")
        target = self.directory / (rid + ".bin")
        try:
            with os.fdopen(
                os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
            ) as stream:
                stream.write(body)
            with self.app.db.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                user, _ = managed(c, user, cid)
                self.check_capacity(c, cid)
                c.execute(
                    "INSERT INTO learning_resources(id,course_id,uploader_id,filename,mime_type,size,sha256,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        rid,
                        cid,
                        user["id"],
                        name,
                        mime,
                        size,
                        hashlib.sha256(body).hexdigest(),
                        now_iso(),
                    ),
                )
                self.event(
                    c,
                    user,
                    rid,
                    "uploaded",
                    "자료를 비공개로 업로드했습니다. 아직 공개 검수 전입니다.",
                )
                row = c.execute(
                    "SELECT * FROM learning_resources WHERE id=?", (rid,)
                ).fetchone()
                return {"resource": self.view(row, user["role"] == "admin")}
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def review(self, user, cid, rid, data):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, _ = managed(c, user, cid, True)
            row = c.execute(
                "SELECT * FROM learning_resources WHERE id=? AND course_id=?",
                (rid, cid),
            ).fetchone()
            if not row:
                raise ApiError(404, "학습 자료를 찾을 수 없습니다.")
            version(data, row)
            action = choice_field(data, "action", ("approve", "reject", "withdraw"))
            if row["status"] != ("approved" if action == "withdraw" else "pending"):
                raise ApiError(409, "현재 자료 상태에서는 처리할 수 없습니다.")
            reason = text_field(data, "reason", minimum=5, maximum=2000)
            if action == "approve":
                target = self.directory / (rid + ".bin")
                if target.is_symlink() or not target.is_file():
                    raise ApiError(409, "검수할 원본 파일을 확인할 수 없습니다.")
                with target.open("rb") as stream:
                    content = stream.read(MAX_BYTES + 1)
                if (
                    len(content) != row["size"]
                    or hashlib.sha256(content).hexdigest() != row["sha256"]
                ):
                    raise ApiError(409, "원본 파일의 무결성을 확인할 수 없습니다.")
            reference = (
                text_field(data, "approvalReference", minimum=5, maximum=2000)
                if action == "approve"
                else row["approval_reference"]
            )
            c.execute(
                "UPDATE learning_resources SET status=?,approval_reference=?,review_note=?,reviewer_id=?,version=version+1,reviewed_at=? WHERE id=?",
                (
                    {
                        "approve": "approved",
                        "reject": "rejected",
                        "withdraw": "withdrawn",
                    }[action],
                    reference,
                    reason,
                    user["id"],
                    now_iso(),
                    rid,
                ),
            )
            self.event(c, user, rid, action, reason)
            return {
                "resource": self.view(
                    c.execute(
                        "SELECT * FROM learning_resources WHERE id=?", (rid,)
                    ).fetchone(),
                    True,
                )
            }

    def check_capacity(self, c, cid):
        if (
            c.execute(
                "SELECT COUNT(*) FROM learning_resources WHERE course_id=?", (cid,)
            ).fetchone()[0]
            >= 1000
        ):
            raise ApiError(
                409, "자료 보관 한도를 확인해주세요. 강의당 최대 1,000개입니다."
            )

    def download(self, h, user, cid, rid, lesson_id=None):
        with self.app.db.connect() as c:
            if lesson_id is None:
                managed(c, user, cid)
            else:
                actor = (
                    c.execute(
                        "SELECT * FROM users WHERE id=? AND status='active'",
                        (user["id"],),
                    ).fetchone()
                    if user
                    else None
                )
                if not actor:
                    raise ApiError(401, "로그인 후 수강 자료를 확인해주세요.")
                course = c.execute(
                    "SELECT * FROM courses WHERE id=? AND status IN ('published','archived')",
                    (cid,),
                ).fetchone()
                if not course or not (
                    can_manage_course(c, actor, course) or has_access(c, actor, cid)
                ):
                    raise ApiError(403, "현재 수강권이 필요한 학습 자료입니다.")
                lesson = c.execute(
                    "SELECT resources FROM course_lessons WHERE course_id=? AND id=? AND status='published'",
                    (cid, lesson_id),
                ).fetchone()
                if not lesson or rid not in [
                    r["assetId"] for r in json.loads(lesson[0])
                ]:
                    raise ApiError(
                        404, "이 차시에 연결된 학습 자료를 찾을 수 없습니다."
                    )
            row = c.execute(
                "SELECT * FROM learning_resources WHERE id=? AND course_id=?",
                (rid, cid),
            ).fetchone()
            if not row:
                raise ApiError(404, "학습 자료를 찾을 수 없습니다.")
            if lesson_id is not None and row["status"] != "approved":
                raise ApiError(
                    410, "현재 제공하지 않는 학습 자료입니다. 담당자에게 문의해주세요."
                )
        target = self.directory / (rid + ".bin")
        if target.is_symlink() or not target.is_file():
            raise ApiError(404, "학습 자료 파일을 찾을 수 없습니다.")
        with target.open("rb") as stream:
            content = stream.read(MAX_BYTES + 1)
            if (
                len(content) != row["size"]
                or hashlib.sha256(content).hexdigest() != row["sha256"]
            ):
                raise ApiError(
                    409,
                    "자료 파일의 무결성을 확인할 수 없습니다. 담당자에게 문의해주세요.",
                )
            h.send_response(200)
            h.send_header("Content-Type", row["mime_type"])
            h.send_header("Content-Length", str(row["size"]))
            h.send_header(
                "Content-Disposition",
                "attachment; filename*=UTF-8''" + quote(row["filename"]),
            )
            h.send_header("Cache-Control", "private, no-store")
            h.security_headers()
            h.end_headers()
            if h.command != "HEAD":
                h.wfile.write(content)
