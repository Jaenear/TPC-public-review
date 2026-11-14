"""Resumable private MP4 originals. Chunk receipts outlive temporary bytes.

Only completed originals enter media_assets. File/DB crash boundaries are
recoverable with deterministic names and checksums; no published file is purged.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from ..core import ApiError, new_id, now_iso, text_field, choice_field
from .common import audit, page_args, version
from .pricing import managed, encode

MAX_BYTES = 100 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
UPLOAD_TTL = 24 * 3600
MAX_PENDING = 5
HEX = re.compile(r"[0-9a-f]{64}")


def checksum(path, maximum):
    try:
        if path.is_symlink() or not path.is_file():
            return None
        hasher, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    return None
                hasher.update(chunk)
        return size, hasher.hexdigest()
    except OSError:
        return None


def sha_field(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ApiError(400, "파일의 SHA-256 확인값이 필요합니다.")
    return value


class MediaUploadService:
    def __init__(self, app):
        self.app = app
        self.clock = time.time
        self.directory = Path(app.db.path).parent / (
            Path(app.db.path).stem + "-uploads"
        )
        self.directory.mkdir(mode=0o700, exist_ok=True)
        if self.directory.is_symlink():
            raise RuntimeError("Private upload directory cannot be a symlink")

    def folder(self, uid, create=False):
        if not re.fullmatch(r"upload_[a-z0-9]{32}", uid):
            raise ApiError(404, "업로드 기록을 찾을 수 없습니다.")
        target = self.directory / uid
        if target.is_symlink():
            raise ApiError(409, "임시 업로드 경로를 확인할 수 없습니다.")
        if create:
            target.mkdir(mode=0o700, exist_ok=True)
        return target

    def asset_id(self, uid):
        return "media_" + uid.removeprefix("upload_")

    def asset_path(self, uid):
        return self.app.platform.content.media_dir / (self.asset_id(uid) + ".mp4")

    def expired(self, row):
        return (
            row["status"] in ("uploading", "ready")
            and self.clock() >= row["expires_at"]
        )

    def owned(self, c, user, cid, uid):
        user, course = managed(c, user, cid)
        row = c.execute(
            "SELECT * FROM media_uploads WHERE id=? AND course_id=?", (uid, cid)
        ).fetchone()
        if not row or (user["role"] != "admin" and row["uploader_id"] != user["id"]):
            raise ApiError(404, "본인의 업로드 기록을 찾을 수 없습니다.")
        return user, course, row

    def open(self, row):
        if self.expired(row):
            raise ApiError(
                410,
                "임시 업로드 보관 기한이 지났습니다. 임시 파일을 정리하고 새 업로드를 시작해주세요.",
            )
        if row["status"] not in ("uploading", "ready"):
            raise ApiError(409, "이미 완료하거나 취소한 업로드입니다.")

    def view(self, c, row, inspect=False):
        asset = (
            c.execute(
                "SELECT id,filename,size,created_at FROM media_assets WHERE id=?",
                (row["asset_id"],),
            ).fetchone()
            if row["asset_id"]
            else None
        )
        result = {
            "id": row["id"],
            "courseId": row["course_id"],
            "uploaderId": row["uploader_id"],
            "filename": row["filename"],
            "size": row["size"],
            "sha256": row["sha256"],
            "offset": row["offset"],
            "chunkSize": CHUNK_BYTES,
            "status": "expired" if self.expired(row) else row["status"],
            "version": row["version"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "expiresAt": datetime.fromtimestamp(row["expires_at"], timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "asset": dict(asset) if asset else None,
            "missingChunks": [],
            "cleanupPending": self.folder(row["id"]).exists(),
        }
        if (
            inspect
            and row["status"] in ("uploading", "ready")
            and not self.expired(row)
        ):
            for receipt in c.execute(
                "SELECT * FROM media_upload_chunks WHERE upload_id=? ORDER BY offset",
                (row["id"],),
            ):
                path = self.folder(row["id"]) / (
                    str(receipt["offset"]).zfill(9) + ".chunk"
                )
                if checksum(path, CHUNK_BYTES) != (receipt["size"], receipt["sha256"]):
                    result["missingChunks"].append(receipt["offset"])
        return result

    def event(self, c, user, uid, action, details=None):
        c.execute(
            "INSERT INTO media_upload_events VALUES(?,?,?,?,?,?)",
            (
                new_id("uploadevent"),
                uid,
                user["id"],
                action,
                encode(details or {}),
                now_iso(),
            ),
        )
        audit(c, user, "media_upload." + action, "media_upload", uid, details)

    def listing(self, user, cid, query):
        with self.app.db.connect() as c:
            user, course = managed(c, user, cid)
            page, limit, offset = page_args(query)
            state = query.get("status", ["pending"])[0]
            if state not in ("pending", "completed", "cancelled", "all"):
                raise ApiError(400, "업로드 상태를 확인해주세요.")
            where, args = "WHERE course_id=?", [cid]
            if user["role"] != "admin":
                where += " AND uploader_id=?"
                args.append(user["id"])
            if state == "pending":
                where += " AND status IN ('uploading','ready')"
            elif state != "all":
                where += " AND status=?"
                args.append(state)
            rows = c.execute(
                "SELECT * FROM media_uploads "
                + where
                + " ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?",
                args + [limit, offset],
            ).fetchall()
            return {
                "courseId": cid,
                "courseTitle": course["title"],
                "uploads": [self.view(c, row) for row in rows],
                "page": page,
                "total": c.execute(
                    "SELECT COUNT(*) FROM media_uploads " + where, args
                ).fetchone()[0],
                "maxBytes": MAX_BYTES,
                "chunkSize": CHUNK_BYTES,
                "ttlSeconds": UPLOAD_TTL,
                "maxPending": MAX_PENDING,
            }

    def detail(self, user, cid, uid):
        with self.app.db.connect() as c:
            _, _, row = self.owned(c, user, cid, uid)
            events = [
                {**dict(r), "details": json.loads(r["details"])}
                for r in c.execute(
                    "SELECT * FROM media_upload_events WHERE upload_id=? ORDER BY created_at DESC,rowid DESC LIMIT 20",
                    (uid,),
                )
            ]
            return {"upload": self.view(c, row, True), "events": events}

    def new(self, user, cid, data):
        filename = text_field(data, "filename", minimum=5, maximum=150)
        if (
            not filename.lower().endswith(".mp4")
            or any(ch in filename for ch in "/\\\r\n")
            or any(ord(ch) < 32 for ch in filename)
        ):
            raise ApiError(400, "폴더 경로가 없는 MP4 파일명을 사용해주세요.")
        size = data.get("size")
        if type(size) is not int or not 32 <= size <= MAX_BYTES:
            raise ApiError(413, "MP4 영상은 32바이트~100MB로 업로드해주세요.")
        sha = sha_field(data.get("sha256"))
        key = text_field(data, "requestKey", minimum=8, maximum=100)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise ApiError(400, "업로드 요청 키를 확인해주세요.")
        with self.app.db.connect() as c:
            user, _ = managed(c, user, cid)
            existing = c.execute(
                "SELECT * FROM media_uploads WHERE course_id=? AND uploader_id=? AND request_key=?",
                (cid, user["id"], key),
            ).fetchone()
            if existing:
                return self.reuse(c, existing, filename, size, sha)
        self.app.limiter.check(("media-start", user["id"]), 30, 3600)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, _ = managed(c, user, cid)
            existing = c.execute(
                "SELECT * FROM media_uploads WHERE course_id=? AND uploader_id=? AND request_key=?",
                (cid, user["id"], key),
            ).fetchone()
            if existing:
                return self.reuse(c, existing, filename, size, sha)
            if (
                c.execute(
                    "SELECT COUNT(*) FROM media_uploads WHERE uploader_id=? AND status IN ('uploading','ready')",
                    (user["id"],),
                ).fetchone()[0]
                >= MAX_PENDING
            ):
                raise ApiError(
                    409,
                    "미완료 업로드는 계정별5개까지 보관합니다. 만료/불필요한 임시 파일을 먼저 정리해주세요.",
                )
            if (
                c.execute(
                    "SELECT COUNT(*) FROM media_assets WHERE course_id=?", (cid,)
                ).fetchone()[0]
                >= 1000
            ):
                raise ApiError(409, "이 강의의 영상 보관 한도에 도달했습니다.")
            uid, timestamp = new_id("upload"), now_iso()
            c.execute(
                "INSERT INTO media_uploads(id,course_id,uploader_id,request_key,filename,size,sha256,created_at,updated_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    uid,
                    cid,
                    user["id"],
                    key,
                    filename,
                    size,
                    sha,
                    timestamp,
                    timestamp,
                    int(self.clock()) + UPLOAD_TTL,
                ),
            )
            self.event(c, user, uid, "created", {"size": size, "sha256": sha})
            row = c.execute("SELECT * FROM media_uploads WHERE id=?", (uid,)).fetchone()
            return {"upload": self.view(c, row)}

    def reuse(self, c, row, filename, size, sha):
        if (row["filename"], row["size"], row["sha256"]) != (filename, size, sha):
            raise ApiError(409, "같은 요청 키에 다른 파일을 보낼 수 없습니다.")
        return {"upload": self.view(c, row)}

    def put(self, h, user, cid, uid):
        try:
            offset = int(h.headers.get("Upload-Offset", ""))
            length = int(h.headers.get("Content-Length", ""))
        except ValueError:
            raise ApiError(400, "전송 위치와 조각 크기를 확인해주세요.")
        if not 0 <= offset < MAX_BYTES or not 1 <= length <= CHUNK_BYTES:
            raise ApiError(413, "영상 조각은 1MB 이내로 전송해주세요.")
        if (
            h.headers.get("Transfer-Encoding")
            or h.headers.get("Content-Type", "").split(";")[0]
            != "application/octet-stream"
        ):
            raise ApiError(415, "바이너리 영상 조각과 고정 크기가 필요합니다.")
        sha = sha_field(h.headers.get("X-Chunk-SHA256"))
        with self.app.db.connect() as c:
            user, _, row = self.owned(c, user, cid, uid)
            self.open(row)
            if offset > row["offset"] or length != min(
                CHUNK_BYTES, row["size"] - offset
            ):
                raise ApiError(
                    409, "서버가 확인한 전송 위치와 조각 크기를 다시 조회해주세요."
                )
        self.app.limiter.check(("media-chunk", user["id"]), 6000, 3600)
        h.connection.settimeout(30)
        try:
            body = h.rfile.read(length)
        except (TimeoutError, OSError):
            raise ApiError(
                400, "영상 조각 전송이 중단됐습니다. 확인한 위치부터 다시 보내주세요."
            )
        if len(body) != length or hashlib.sha256(body).hexdigest() != sha:
            raise ApiError(
                400, "영상 조각이 완전히 전달되지 않았거나 확인값이 다릅니다."
            )
        if offset == 0 and (len(body) < 12 or body[4:8] != b"ftyp"):
            raise ApiError(400, "MP4 식별 헤더를 확인해주세요.")
        folder = self.folder(uid, True)
        stage = folder / (new_id("stage") + ".part")
        target = folder / (str(offset).zfill(9) + ".chunk")
        replaced = False
        try:
            fd = os.open(stage, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            with self.app.db.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                user, _, row = self.owned(c, user, cid, uid)
                self.open(row)
                receipt = c.execute(
                    "SELECT * FROM media_upload_chunks WHERE upload_id=? AND offset=?",
                    (uid, offset),
                ).fetchone()
                if receipt:
                    if (receipt["size"], receipt["sha256"]) != (length, sha):
                        raise ApiError(
                            409, "이미 확인한 조각과 다른 내용을 보낼 수 없습니다."
                        )
                    if checksum(target, CHUNK_BYTES) != (length, sha):
                        if target.is_symlink():
                            raise ApiError(409, "임시 조각 경로를 확인해주세요.")
                        os.replace(stage, target)
                        replaced = True
                        self.event(
                            c, user, uid, "restored", {"offset": offset, "size": length}
                        )
                else:
                    if (
                        row["status"] != "uploading"
                        or row["offset"] != offset
                        or length != min(CHUNK_BYTES, row["size"] - offset)
                    ):
                        raise ApiError(
                            409,
                            "서버가 확인한 위치가 바뀌었습니다. 진행 상태를 다시 조회해주세요.",
                        )
                    if target.is_symlink():
                        raise ApiError(409, "임시 조각 경로를 확인해주세요.")
                    os.replace(stage, target)
                    replaced = True
                    c.execute(
                        "INSERT INTO media_upload_chunks VALUES(?,?,?,?,?)",
                        (uid, offset, length, sha, now_iso()),
                    )
                    end = offset + length
                    c.execute(
                        "UPDATE media_uploads SET offset=?,status=?,version=version+1,updated_at=? WHERE id=?",
                        (
                            end,
                            "ready" if end == row["size"] else "uploading",
                            now_iso(),
                            uid,
                        ),
                    )
                    self.event(
                        c,
                        user,
                        uid,
                        "chunk",
                        {"offset": offset, "size": length, "sha256": sha},
                    )
                result = {
                    "upload": self.view(
                        c,
                        c.execute(
                            "SELECT * FROM media_uploads WHERE id=?", (uid,)
                        ).fetchone(),
                    )
                }
            return result
        except Exception:
            if replaced:
                with self.app.db.connect() as c:
                    committed = c.execute(
                        "SELECT 1 FROM media_upload_chunks WHERE upload_id=? AND offset=?",
                        (uid, offset),
                    ).fetchone()
                if not committed:
                    target.unlink(missing_ok=True)
            raise
        finally:
            stage.unlink(missing_ok=True)

    def complete(self, user, cid, uid, data):
        created_file = False
        assemble = self.folder(uid) / "assemble.part"
        target = self.asset_path(uid)
        try:
            with self.app.db.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                user, _, row = self.owned(c, user, cid, uid)
                if row["status"] == "completed":
                    if checksum(target, MAX_BYTES) != (row["size"], row["sha256"]):
                        raise ApiError(
                            409,
                            "완료된 원본 파일을 확인할 수 없습니다. 관리자에게 문의해주세요.",
                        )
                    result = {"upload": self.view(c, row)}
                else:
                    self.open(row)
                    version(data, row)
                    if row["status"] != "ready":
                        raise ApiError(409, "모든 영상 조각이 확인된 뒤 완료해주세요.")
                    folder = self.folder(uid, True)
                    if assemble.is_symlink() or target.is_symlink():
                        raise ApiError(409, "영상 원본 경로를 확인해주세요.")
                    if c.execute(
                        "SELECT 1 FROM media_assets WHERE id=?", (self.asset_id(uid),)
                    ).fetchone():
                        raise ApiError(409, "원본 영수증을 확인할 수 없습니다.")
                    hasher, position = hashlib.sha256(), 0
                    fd = os.open(assemble, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        for receipt in c.execute(
                            "SELECT * FROM media_upload_chunks WHERE upload_id=? ORDER BY offset",
                            (uid,),
                        ):
                            path = folder / (str(receipt["offset"]).zfill(9) + ".chunk")
                            if receipt["offset"] != position or checksum(
                                path, CHUNK_BYTES
                            ) != (receipt["size"], receipt["sha256"]):
                                raise ApiError(
                                    409,
                                    "복구할 영상 조각이 있습니다. 같은 파일을 선택하고 전송 상태를 다시 확인해주세요.",
                                )
                            with path.open("rb") as part:
                                body = part.read(CHUNK_BYTES + 1)
                            hasher.update(body)
                            position += len(body)
                            stream.write(body)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if (position, hasher.hexdigest()) != (row["size"], row["sha256"]):
                        raise ApiError(
                            409,
                            "전체 파일의 크기/SHA-256이 다릅니다. 같은 원본으로 새 업로드를 시작해주세요.",
                        )
                    with assemble.open("rb") as stream:
                        if stream.read(12)[4:8] != b"ftyp":
                            raise ApiError(400, "MP4 식별 헤더를 확인해주세요.")
                    if target.exists():
                        if checksum(target, MAX_BYTES) != (row["size"], row["sha256"]):
                            raise ApiError(
                                409,
                                "복구할 원본 파일의 확인값이 다릅니다. 관리자에게 문의해주세요.",
                            )
                    else:
                        os.replace(assemble, target)
                        created_file = True
                    asset_id = self.asset_id(uid)
                    timestamp = now_iso()
                    c.execute(
                        "INSERT INTO media_assets VALUES(?,?,?,?,?,?,?)",
                        (
                            asset_id,
                            cid,
                            row["uploader_id"],
                            row["filename"],
                            row["size"],
                            row["sha256"],
                            timestamp,
                        ),
                    )
                    c.execute(
                        "UPDATE media_uploads SET status='completed',asset_id=?,version=version+1,updated_at=?,finished_at=? WHERE id=?",
                        (asset_id, timestamp, timestamp, uid),
                    )
                    self.event(
                        c,
                        user,
                        uid,
                        "completed",
                        {
                            "assetId": asset_id,
                            "size": row["size"],
                            "sha256": row["sha256"],
                        },
                    )
                    audit(
                        c,
                        user,
                        "media.uploaded",
                        "course",
                        cid,
                        {"assetId": asset_id, "uploadId": uid, "size": row["size"]},
                    )
                    result = {
                        "upload": self.view(
                            c,
                            c.execute(
                                "SELECT * FROM media_uploads WHERE id=?", (uid,)
                            ).fetchone(),
                        )
                    }
            result["upload"]["cleanupPending"] = not self.purge(uid, False)
            return result
        except Exception:
            if created_file:
                with self.app.db.connect() as c:
                    committed = c.execute(
                        "SELECT 1 FROM media_assets WHERE id=?", (self.asset_id(uid),)
                    ).fetchone()
                if not committed:
                    target.unlink(missing_ok=True)
            raise
        finally:
            assemble.unlink(missing_ok=True)

    def purge(self, uid, remove_orphan):
        """Only this upload's known temporary names; never completed originals."""
        try:
            folder = self.folder(uid)
            if folder.exists():
                for path in folder.iterdir():
                    if (
                        path.is_symlink()
                        or not path.is_file()
                        or not (
                            re.fullmatch(
                                r"\d{9}\.chunk|stage_[a-z0-9]{32}\.part", path.name
                            )
                            or path.name == "assemble.part"
                        )
                    ):
                        return False
                for path in folder.iterdir():
                    path.unlink()
                folder.rmdir()
            if remove_orphan:
                with self.app.db.connect() as c:
                    exists = c.execute(
                        "SELECT 1 FROM media_assets WHERE id=?", (self.asset_id(uid),)
                    ).fetchone()
                path = self.asset_path(uid)
                if not exists:
                    if path.is_symlink():
                        return False
                    path.unlink(missing_ok=True)
            return True
        except (OSError, ApiError):
            return False

    def cancel(self, user, cid, uid, data):
        reason = text_field(data, "reason", minimum=5, maximum=2000)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, _, row = self.owned(c, user, cid, uid)
            if row["status"] == "completed":
                raise ApiError(
                    409, "완료된 원본은 임시 업로드 취소로 삭제하지 않습니다."
                )
            if row["status"] != "cancelled":
                version(data, row)
                c.execute(
                    "UPDATE media_uploads SET status='cancelled',version=version+1,updated_at=?,finished_at=? WHERE id=?",
                    (now_iso(), now_iso(), uid),
                )
                self.event(c, user, uid, "cancelled", {"reason": reason})
            result = {
                "upload": self.view(
                    c,
                    c.execute(
                        "SELECT * FROM media_uploads WHERE id=?", (uid,)
                    ).fetchone(),
                )
            }
        result["upload"]["cleanupPending"] = not self.purge(uid, True)
        return result

    def cleanup(self, user, cid, data):
        ids = data.get("uploadIds")
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 20
            or any(not isinstance(uid, str) for uid in ids)
            or len(ids) != len(set(ids))
        ):
            raise ApiError(400, "정리할 임시 업로드1~20개를 명시해주세요.")
        reason = text_field(data, "reason", minimum=5, maximum=2000)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            for uid in ids:
                actor, _, row = self.owned(c, user, cid, uid)
                if row["status"] in ("uploading", "ready") and not self.expired(row):
                    raise ApiError(
                        409,
                        "보관 중인 업로드는 개별 취소 또는 기한 만료 뒤 정리해주세요.",
                    )
                if row["status"] in ("uploading", "ready"):
                    c.execute(
                        "UPDATE media_uploads SET status='cancelled',version=version+1,updated_at=?,finished_at=? WHERE id=?",
                        (now_iso(), now_iso(), uid),
                    )
                self.event(c, actor, uid, "cleanup_requested", {"reason": reason})
        outcomes = []
        for uid in ids:
            with self.app.db.connect() as c:
                row = c.execute(
                    "SELECT status FROM media_uploads WHERE id=?", (uid,)
                ).fetchone()
            outcomes.append(
                {
                    "id": uid,
                    "cleanupPending": not self.purge(uid, row["status"] != "completed"),
                }
            )
        return {"uploads": outcomes}
