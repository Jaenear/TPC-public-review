"""Immutable video-bound caption originals and atomic, explicit operator review."""

import hashlib
import json
from urllib.parse import unquote
import re
from ..core import ApiError, new_id, now_iso, text_field, choice_field
from ..webvtt import validate, language, MAX_BYTES, PROFILE
from .pricing import managed as _managed
from .common import audit, page_args
from .publications import file_matches

KEY = re.compile(r"[A-Za-z0-9_-]{8,120}")
HASH = re.compile(r"[a-f0-9]{64}")
JOIN = "SELECT a.*,s.status,s.version,s.review_note,s.approval_reference,s.updated_at FROM video_captions a JOIN video_caption_states s ON s.caption_id=a.id "


def managed(c, user, cid, admin=False):
    try:
        return _managed(c, user, cid, admin)
    except ApiError as error:
        if error.status == 401:
            raise ApiError(401, "현재 계정으로 로그인하고 자막을 확인해주세요.")
        if error.status == 403:
            raise ApiError(
                403, "자막 관리는 현재 담당 코치와 관리자만 이용할 수 있습니다."
            )
        raise


def digest(value):
    try:
        return hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
    except (ValueError, TypeError, RecursionError):
        raise ApiError(400, "유효한 자막 요청 형식을 확인해주세요.")


def key_field(data):
    key = text_field(data, "requestKey", minimum=8, maximum=120)
    if not KEY.fullmatch(key):
        raise ApiError(400, "자막 요청 식별자를 확인해주세요.")
    return key


def int_version(value):
    if type(value) is not int or not 1 <= value <= 9007199254740991:
        raise ApiError(400, "자막 판본을 확인해주세요.")
    return value


class CaptionService:
    def __init__(self, app):
        self.app = app

    def asset(self, c, cid, aid):
        row = c.execute(
            "SELECT * FROM media_assets WHERE id=? AND course_id=?", (aid, cid)
        ).fetchone()
        if not row:
            raise ApiError(404, "이 강의의 영상 원본을 찾을 수 없습니다.")
        return row

    def original(self, cid, aid, user, c=None):
        if c is None:
            with self.app.db.connect() as c:
                return self.original(cid, aid, user, c)
        actor, course = managed(c, user, cid)
        asset = self.asset(c, cid, aid)
        return actor, course, asset

    def get(self, c, cid, rid):
        row = c.execute(JOIN + "WHERE a.id=? AND a.course_id=?", (rid, cid)).fetchone()
        if not row:
            raise ApiError(404, "자막을 찾을 수 없습니다.")
        return row

    def view(self, row, operator=False):
        value = {
            "id": row["id"],
            "mediaAssetId": row["media_asset_id"],
            "language": row["language"],
            "label": row["label"],
            "kind": row["kind"],
            "cueCount": row["cue_count"],
            "endSeconds": row["end_seconds"],
        }
        if operator:
            value.update(
                {
                    "courseId": row["course_id"],
                    "filename": row["filename"],
                    "size": row["size"],
                    "sha256": row["sha256"],
                    "mediaSha256": row["media_sha256"],
                    "profile": row["profile"],
                    "status": row["status"],
                    "version": row["version"],
                    "reviewNote": row["review_note"],
                    "approvalReference": row["approval_reference"],
                    "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"],
                }
            )
        return value

    def active(self, c, aid, lang, kind):
        return c.execute(
            JOIN
            + "WHERE a.media_asset_id=? AND a.language=? AND a.kind=? AND s.status='approved'",
            (aid, lang, kind),
        ).fetchone()

    def event(self, c, actor, row, action, reason, key, reqhash, related=None):
        eid = new_id("captionevent")
        c.execute(
            "INSERT INTO video_caption_events VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                eid,
                row["id"],
                actor["id"],
                action,
                reason,
                key,
                reqhash,
                row["version"],
                related,
                now_iso(),
            ),
        )
        audit(
            c,
            actor,
            "caption." + action,
            "caption",
            row["id"],
            {
                "mediaAssetId": row["media_asset_id"],
                "eventId": eid,
                "relatedCaptionId": related,
            },
        )
        return eid

    def metadata(self, data):
        if not isinstance(data, dict) or set(data) != {
            "filename",
            "language",
            "label",
            "kind",
            "requestKey",
            "sha256",
            "mediaSha256",
        }:
            raise ApiError(
                400, "자막 파일/언어/표시 이름과 원본 확인값을 확인해주세요."
            )
        name = text_field(data, "filename", minimum=5, maximum=150)
        if (
            "/" in name
            or "\\" in name
            or not name.lower().endswith(".vtt")
            or any(ord(ch) < 32 for ch in name)
        ):
            raise ApiError(400, "경로 없는 .vtt 파일 이름이 필요합니다.")
        if (
            not isinstance(data["sha256"], str)
            or not HASH.fullmatch(data["sha256"])
            or not isinstance(data["mediaSha256"], str)
            or not HASH.fullmatch(data["mediaSha256"])
        ):
            raise ApiError(400, "영상과 자막 원본 확인값을 확인해주세요.")
        return {
            "filename": name,
            "language": language(data["language"]),
            "label": text_field(data, "label", minimum=2, maximum=80),
            "kind": choice_field(data, "kind", ("subtitles", "captions")),
            "requestKey": key_field(data),
            "sha256": data["sha256"],
            "mediaSha256": data["mediaSha256"],
        }

    def check_capacity(self, c, aid):
        total, pending = c.execute(
            "SELECT COUNT(*),COALESCE(SUM(status='pending'),0) FROM video_caption_states WHERE media_asset_id=?",
            (aid,),
        ).fetchone()
        if total >= 100 or pending >= 20:
            raise ApiError(
                409,
                "이 영상의 자막 보관/검수 대기 한도를 확인해주세요. 운영자에게 문의해주세요.",
            )

    def upload(self, h, user, cid, aid):
        # Authorize before receiving any private file; recheck after receiving it.
        self.original(cid, aid, user)
        raw = h.headers.get("X-Caption-Metadata", "")
        if len(raw) > 10000:
            raise ApiError(400, "자막 메타데이터가 너무 깁니다.")
        try:
            meta = self.metadata(json.loads(unquote(raw)))
        except (ValueError, UnicodeError, RecursionError):
            raise ApiError(400, "자막 요청 헤더를 확인해주세요.")
        mime = h.headers.get("Content-Type", "").split(";")[0].strip().lower()
        try:
            size = int(h.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(400, "자막 크기를 확인해주세요.")
        if mime != "text/vtt":
            raise ApiError(415, "UTF-8 WebVTT(text/vtt) 파일이 필요합니다.")
        if not 8 <= size <= MAX_BYTES:
            raise ApiError(413, "자막은 파일별 1MB 이내로 준비해주세요.")
        h.connection.settimeout(30)
        chunks = []
        remaining = size
        try:
            while remaining:
                part = h.rfile.read(min(65536, remaining))
                if not part:
                    raise ApiError(400, "자막 전송이 완료되지 않았습니다.")
                chunks.append(part)
                remaining -= len(part)
        except OSError:
            raise ApiError(
                400, "자막 전송 연결을 확인하고 같은 파일을 다시 선택해주세요."
            )
        return self.submit(user, cid, aid, meta, b"".join(chunks))

    def submit(self, user, cid, aid, data, body):
        meta = self.metadata(data)
        if hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise ApiError(
                400, "자막 파일 확인값이 다릅니다. 같은 원본 파일을 다시 선택해주세요."
            )
        parsed = validate(body)
        reqhash = digest(meta)
        with self.app.db.connect() as c:
            actor, _, asset = self.original(cid, aid, user, c)
            old = c.execute(
                JOIN + "WHERE a.uploader_id=? AND a.course_id=? AND a.request_key=?",
                (actor["id"], cid, meta["requestKey"]),
            ).fetchone()
            if old:
                return self.replayed_upload(old, reqhash, aid)
        self.app.limiter.check(("caption-upload", actor["id"]), 30, 3600)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            actor, _, asset = self.original(cid, aid, user, c)
            old = c.execute(
                JOIN + "WHERE a.uploader_id=? AND a.course_id=? AND a.request_key=?",
                (actor["id"], cid, meta["requestKey"]),
            ).fetchone()
            if old:
                return self.replayed_upload(old, reqhash, aid)
            if asset["sha256"] != meta["mediaSha256"]:
                raise ApiError(
                    409, "영상 원본이 변경됐습니다. 최신 원본에서 자막을 등록해주세요."
                )
            self.check_capacity(c, aid)
            duration = self.app.platform.watch_progress.metadata(c, asset)[
                "durationSeconds"
            ]
            parsed = validate(body, duration)
            return self.create(c, actor, cid, asset, meta, body, parsed, reqhash)

    def replayed_upload(self, row, reqhash, aid):
        if row["request_hash"] != reqhash or row["media_asset_id"] != aid:
            raise ApiError(409, "같은 자막 등록 요청의 내용이 다릅니다.")
        return {"caption": self.view(row, True), "replayed": True}

    def create(self, c, actor, cid, asset, meta, body, parsed, reqhash):
        rid = new_id("caption")
        at = now_iso()
        c.execute(
            "INSERT INTO video_captions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                cid,
                asset["id"],
                asset["sha256"],
                actor["id"],
                meta["filename"],
                meta["language"],
                meta["label"],
                meta["kind"],
                body,
                len(body),
                meta["sha256"],
                parsed["cueCount"],
                parsed["endSeconds"],
                PROFILE,
                meta["requestKey"],
                reqhash,
                at,
            ),
        )
        c.execute(
            "INSERT INTO video_caption_states(caption_id,media_asset_id,language,kind,status,updated_at) VALUES(?,?,?,?,'pending',?)",
            (rid, asset["id"], meta["language"], meta["kind"], at),
        )
        row = self.get(c, cid, rid)
        self.event(
            c,
            actor,
            row,
            "submitted",
            "자막 원본을 비공개 검수 요청했습니다.",
            meta["requestKey"],
            reqhash,
        )
        return {"caption": self.view(row, True), "replayed": False}

    def listing(self, user, cid, aid, query):
        page, size, offset = page_args(query)
        with self.app.db.connect() as c:
            actor, course, asset = self.original(cid, aid, user, c)
            meta = self.app.platform.watch_progress.metadata(c, asset)
            rows = c.execute(
                JOIN
                + "WHERE a.media_asset_id=? ORDER BY a.rowid DESC LIMIT ? OFFSET ?",
                (aid, size, offset),
            ).fetchall()
            return {
                "captions": [self.view(r, True) for r in rows],
                "page": page,
                "pageSize": size,
                "total": c.execute(
                    "SELECT COUNT(*) FROM video_captions WHERE media_asset_id=?", (aid,)
                ).fetchone()[0],
                "courseTitle": course["title"],
                "video": {
                    "id": aid,
                    "filename": asset["filename"],
                    "sha256": asset["sha256"],
                    **meta,
                },
                "canReview": actor["role"] == "admin",
                "profile": PROFILE,
                "maxBytes": MAX_BYTES,
            }

    def detail(self, user, cid, rid):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor, _ = managed(c, user, cid)
            row = self.get(c, cid, rid)
            active = self.active(c, row["media_asset_id"], row["language"], row["kind"])
            parsed = validate(bytes(row["body"]))
            return {
                "caption": self.view(row, True),
                "activeCaption": self.view(active, True) if active else None,
                "canReview": actor["role"] == "admin",
                "canCancel": row["status"] == "pending"
                and (actor["role"] == "admin" or row["uploader_id"] == actor["id"]),
                "events": [
                    dict(r)
                    for r in c.execute(
                        "SELECT action,reason,result_version,related_caption_id,created_at FROM video_caption_events WHERE caption_id=? ORDER BY rowid",
                        (rid,),
                    )
                ],
                "previewCues": parsed["cues"][:20],
            }

    def verify_original(self, c, row):
        asset = self.asset(c, row["course_id"], row["media_asset_id"])
        target = self.app.platform.content.media_dir / (asset["id"] + ".mp4")
        if asset["sha256"] != row["media_sha256"] or not file_matches(
            target, asset, 100 * 1024 * 1024, True
        ):
            raise ApiError(409, "검수할 영상 원본의 존재/무결성을 확인할 수 없습니다.")
        duration = self.app.platform.watch_progress.metadata(c, asset, cache=True)[
            "durationSeconds"
        ]
        if not duration:
            raise ApiError(409, "영상의 전체 길이를 확인한 뒤 자막을 승인해주세요.")
        body = bytes(row["body"])
        if (
            len(body) != row["size"]
            or hashlib.sha256(body).hexdigest() != row["sha256"]
        ):
            raise ApiError(409, "자막 원본의 무결성을 확인할 수 없습니다.")
        validate(body, duration)

    def review(self, user, cid, rid, data):
        if not isinstance(data, dict) or set(data) - {
            "action",
            "version",
            "activeCaptionId",
            "activeVersion",
            "reason",
            "approvalReference",
            "confirmed",
            "requestKey",
        }:
            raise ApiError(400, "자막 검수 요청 항목을 확인해주세요.")
        key = key_field(data)
        reason = text_field(data, "reason", minimum=5, maximum=2000)
        action = choice_field(
            data, "action", ("approve", "reject", "withdraw", "cancel")
        )
        reqhash = digest({"captionId": rid, **data})
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            actor, _ = managed(c, user, cid, action != "cancel")
            row = self.get(c, cid, rid)
            if (
                action == "cancel"
                and actor["role"] != "admin"
                and row["uploader_id"] != actor["id"]
            ):
                raise ApiError(403, "본인이 요청한 자막만 취소할 수 있습니다.")
            event = c.execute(
                "SELECT id,request_hash FROM video_caption_events WHERE caption_id=? AND actor_id=? AND request_key=?",
                (rid, actor["id"], key),
            ).fetchone()
            if event:
                if event["request_hash"] != reqhash:
                    raise ApiError(409, "같은 자막 검수 요청의 내용이 다릅니다.")
                return {
                    "caption": self.view(row, True),
                    "eventId": event["id"],
                    "replayed": True,
                }
            if int_version(data.get("version")) != row["version"]:
                raise ApiError(
                    409, "다른 창에서 자막을 변경했습니다. 최신 상태를 확인해주세요."
                )
            if row["status"] != ("approved" if action == "withdraw" else "pending"):
                raise ApiError(409, "현재 자막 상태에서는 처리할 수 없습니다.")
            reference = row["approval_reference"]
            active = None
            if action == "approve":
                if data.get("confirmed") is not True:
                    raise ApiError(400, "영상과 자막을 확인한 체크가 필요합니다.")
                reference = text_field(
                    data, "approvalReference", minimum=5, maximum=2000
                )
                self.verify_original(c, row)
                active = self.active(
                    c, row["media_asset_id"], row["language"], row["kind"]
                )
                if "activeCaptionId" not in data or "activeVersion" not in data:
                    raise ApiError(400, "확인한 현재 승인 자막 정보를 함께 보내주세요.")
                if data["activeCaptionId"] is None:
                    if data["activeVersion"] is not None:
                        raise ApiError(400, "승인 자막의 판본을 확인해주세요.")
                else:
                    if not isinstance(data["activeCaptionId"], str):
                        raise ApiError(400, "승인 자막 식별자를 확인해주세요.")
                    int_version(data["activeVersion"])
                if data["activeCaptionId"] != (
                    active["id"] if active else None
                ) or data["activeVersion"] != (active["version"] if active else None):
                    raise ApiError(
                        409,
                        "현재 승인 자막이 변경됐습니다. 교체 대상을 다시 확인해주세요.",
                    )
            return self.decide(
                c, actor, row, action, reason, key, reqhash, reference, active
            )

    def decide(
        self,
        c,
        actor,
        row,
        action,
        reason,
        key,
        reqhash,
        reference,
        active=None,
        sample=False,
    ):
        if active:
            c.execute(
                "UPDATE video_caption_states SET status='superseded',version=version+1,review_note=?,updated_at=? WHERE caption_id=?",
                (reason, now_iso(), active["id"]),
            )
            older = self.get(c, row["course_id"], active["id"])
            self.event(
                c,
                actor,
                older,
                "superseded",
                reason,
                "related_" + digest({"captionId": row["id"], "requestKey": key}),
                reqhash,
                row["id"],
            )
        status = {
            "approve": "approved",
            "reject": "rejected",
            "withdraw": "withdrawn",
            "cancel": "cancelled",
        }[action]
        c.execute(
            "UPDATE video_caption_states SET status=?,version=version+1,review_note=?,approval_reference=?,updated_at=? WHERE caption_id=?",
            (status, reason, reference, now_iso(), row["id"]),
        )
        updated = self.get(c, row["course_id"], row["id"])
        eid = self.event(
            c,
            actor,
            updated,
            "sample_approved" if sample else status,
            reason,
            key,
            reqhash,
            active["id"] if active else None,
        )
        return {"caption": self.view(updated, True), "eventId": eid, "replayed": False}

    def lesson_context(self, c, user, cid, lid, expected=None):
        context = self.app.platform.lesson_learning.context(c, user, cid, lid)
        lesson = context[2]
        if expected is not None and (
            expected["version"] != lesson["version"]
            or expected.get("videoAssetId") != lesson["video_asset_id"]
        ):
            raise ApiError(409, "영상/차시가 변경됐습니다. 최신 수업을 확인해주세요.")
        return lesson

    def learner(self, c, user, cid, lid, expected=None):
        lesson = self.lesson_context(c, user, cid, lid, expected)
        rows = c.execute(
            JOIN
            + "JOIN media_assets m ON m.id=a.media_asset_id WHERE a.course_id=? AND a.media_asset_id=? AND s.status='approved' AND a.media_sha256=m.sha256 ORDER BY a.language,a.kind,a.id",
            (cid, lesson["video_asset_id"]),
        ).fetchall()
        return {
            "captions": [self.view(r) for r in rows],
            "lessonVersion": lesson["version"],
            "videoAssetId": lesson["video_asset_id"],
        }

    def learner_read(self, user, cid, lid):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            return self.learner(c, user, cid, lid)

    def file(self, h, user, cid, rid, lid=None):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            if lid is None:
                managed(c, user, cid)
            else:
                lesson = self.lesson_context(c, user, cid, lid)
            row = self.get(c, cid, rid)
            if lid is not None:
                asset = self.asset(c, cid, row["media_asset_id"])
                if (
                    row["status"] != "approved"
                    or lesson["video_asset_id"] != row["media_asset_id"]
                    or row["media_sha256"] != asset["sha256"]
                ):
                    raise ApiError(404, "현재 제공할 수 있는 승인 자막이 아닙니다.")
            body = bytes(row["body"])
            if (
                len(body) != row["size"]
                or hashlib.sha256(body).hexdigest() != row["sha256"]
            ):
                raise ApiError(409, "자막 원본을 확인할 수 없습니다.")
        h.send_response(200)
        h.send_header("Content-Type", "text/vtt; charset=utf-8")
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "no-store")
        h.security_headers()
        h.end_headers()
        if h.command != "HEAD":
            h.wfile.write(body)
