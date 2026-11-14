"""Own resume point and furthest reached point, isolated by video original.

Seeking can reach a point without watching earlier intervals. These percentages
are positions, never attendance or lesson-completion credit.
"""

import hashlib
import json
import math
import re

from ..access import for_member
from ..core import ApiError, now_iso, text_field
from ..media_metadata import mp4_duration
from .common import can_manage_course, require_course_access
from .learning_start import active_member


def percent(seconds, duration):
    return round(min(100, max(0, seconds / duration * 100)), 1) if duration else None


class WatchProgressService:
    def __init__(self, app):
        self.app = app

    def metadata(self, c, asset, cache=False, sample=False):
        row = c.execute(
            "SELECT * FROM media_playback_metadata WHERE asset_id=?", (asset["id"],)
        ).fetchone()
        if row:
            if row["asset_sha256"] != asset["sha256"]:
                raise ApiError(409, "영상 원본 정보가 변경됐습니다.")
            return {
                "durationSeconds": row["duration_seconds"],
                "isSample": bool(row["is_sample"]),
            }
        path = self.app.platform.content.media_dir / (asset["id"] + ".mp4")
        duration = (
            mp4_duration(path)
            if path.is_file()
            and not path.is_symlink()
            and path.stat().st_size == asset["size"]
            else None
        )
        if duration and cache:
            c.execute(
                "INSERT INTO media_playback_metadata(asset_id,asset_sha256,duration_seconds,source,is_sample,created_at) VALUES(?,?,?,'mp4_mvhd',?,?)",
                (asset["id"], asset["sha256"], duration, int(sample), now_iso()),
            )
        return {"durationSeconds": duration, "isSample": sample if duration else False}

    def state(self, c, user, course, lesson, cache=False):
        aid = lesson["video_asset_id"]
        access = for_member(c, user["id"] if user else "", course["id"])
        can_save = bool(
            user and (access["canAccess"] or can_manage_course(c, user, course))
        )
        asset = (
            c.execute(
                "SELECT * FROM media_assets WHERE id=? AND course_id=?",
                (aid, course["id"]),
            ).fetchone()
            if aid
            else None
        )
        meta = (
            self.metadata(c, asset, cache=cache)
            if asset
            else {"durationSeconds": None, "isSample": False}
        )
        duration = meta["durationSeconds"]
        uid = user["id"] if user else ""
        args = (uid, course["id"], lesson["id"])
        row = (
            c.execute(
                "SELECT * FROM lesson_video_progress WHERE user_id=? AND course_id=? AND lesson_id=? AND asset_id=?",
                args + (aid,),
            ).fetchone()
            if aid
            else None
        )
        legacy = c.execute(
            "SELECT w.seconds,w.updated_at,s.asset_id FROM lesson_watch w LEFT JOIN lesson_watch_sources s ON s.user_id=w.user_id AND s.course_id=w.course_id AND s.lesson_id=w.lesson_id WHERE w.user_id=? AND w.course_id=? AND w.lesson_id=?",
            args,
        ).fetchone()
        seconds, furthest, version, at, source, reset = 0, 0, 0, None, "none", None
        if row:
            seconds, furthest, version, at, source = (
                row["seconds"],
                row["furthest_seconds"],
                row["version"],
                row["updated_at"],
                "video_records",
            )
        elif legacy and aid and legacy["asset_id"] == aid:
            seconds = furthest = legacy["seconds"]
            at, source = legacy["updated_at"], "legacy_checkpoint"
        elif legacy and aid:
            reset = "video_changed" if legacy["asset_id"] else "unverified_source"
        if duration and (seconds > duration + 1 or furthest > duration + 1):
            seconds = furthest = 0
            reset, source = "checkpoint_out_of_range", "none"
        if duration:
            seconds, furthest = min(seconds, duration), min(furthest, duration)
        return {
            "assetId": aid,
            "seconds": seconds,
            "furthestSeconds": furthest,
            "durationSeconds": duration,
            "positionPercent": percent(seconds, duration),
            "furthestPercent": percent(furthest, duration),
            "version": version,
            "updatedAt": at,
            "source": source,
            "resetReason": reset,
            "canSave": can_save,
            "isSample": meta["isSample"],
        }

    def attach(self, c, user, cid, lid, expected):
        user = active_member(c, user)
        course = c.execute("SELECT * FROM courses WHERE id=?", (cid,)).fetchone()
        lesson = c.execute(
            "SELECT * FROM course_lessons WHERE course_id=? AND id=?", (cid, lid)
        ).fetchone()
        if (
            not course
            or not lesson
            or course["status"] not in ("published", "archived")
            or lesson["status"] != "published"
        ):
            raise ApiError(404, "현재 학습할 수 없는 차시입니다.")
        if lesson["version"] != expected["version"] or lesson[
            "video_asset_id"
        ] != expected.get("videoAssetId"):
            raise ApiError(409, "차시가 변경됐습니다. 최신 수업을 확인해주세요.")
        if not (
            course["status"] == "published" and lesson["free"]
        ) and not can_manage_course(c, user, course):
            require_course_access(c, user, course)
        return self.state(c, user, course, lesson)

    def save(self, user, data):
        cid = text_field(data, "courseId", maximum=100)
        lid = text_field(data, "lessonId", maximum=100)
        seconds = data.get("seconds")
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or not 0 <= seconds <= 86400
        ):
            raise ApiError(400, "재생 시간을 확인해주세요.")
        key = data.get("requestKey")
        identified = "requestKey" in data or "watchVersion" in data
        if identified and (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{8,120}", key)
            or type(data.get("watchVersion")) is not int
            or not 0 <= data["watchVersion"] <= 9007199254740991
            or not isinstance(data.get("videoAssetId"), str)
            or type(data.get("lessonVersion")) is not int
        ):
            raise ApiError(400, "시청 저장 요청의 식별자와 판본을 확인해주세요.")
        streamed = "streamId" in data or "sequence" in data
        stream = data.get("streamId")
        sequence = data.get("sequence")
        if streamed and (
            not identified
            or not isinstance(stream, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{8,120}", stream)
            or type(sequence) is not int
            or not 1 <= sequence <= 9007199254740991
        ):
            raise ApiError(400, "재생 세션과 시청 순서를 확인해주세요.")
        request_hash = (
            hashlib.sha256(
                json.dumps(
                    {
                        k: data.get(k)
                        for k in (
                            "seconds",
                            "videoAssetId",
                            "lessonVersion",
                            "watchVersion",
                            "streamId",
                            "sequence",
                        )
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if identified
            else None
        )
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = active_member(c, user)
            if not user:
                raise ApiError(401, "로그인 후 이용해주세요.")
            course = self.app.platform.discussions.course(c, cid)
            require_course_access(c, user, course)
            lesson = c.execute(
                "SELECT * FROM course_lessons WHERE course_id=? AND id=?", (cid, lid)
            ).fetchone()
            if not lesson or (
                lesson["status"] != "published"
                and not can_manage_course(c, user, course)
            ):
                raise ApiError(404, "차시를 찾을 수 없습니다.")
            if (
                "videoAssetId" in data
                and data["videoAssetId"] != lesson["video_asset_id"]
            ) or (
                "lessonVersion" in data
                and (
                    type(data["lessonVersion"]) is not int
                    or data["lessonVersion"] != lesson["version"]
                )
            ):
                raise ApiError(
                    409,
                    "시청 중인 차시/영상이 바뀌었습니다. 최신 수업에서 이어가주세요.",
                )
            aid = lesson["video_asset_id"]
            current = self.state(c, user, course, lesson, cache=bool(aid))
            args = (user["id"], cid, lid, aid)
            row = (
                c.execute(
                    "SELECT * FROM lesson_video_progress WHERE user_id=? AND course_id=? AND lesson_id=? AND asset_id=?",
                    args,
                ).fetchone()
                if aid
                else None
            )
            if identified and row and row["last_request_key"] == key:
                if row["last_request_hash"] != request_hash:
                    raise ApiError(409, "같은 시청 저장 요청의 내용이 다릅니다.")
                return {
                    "seconds": current["seconds"],
                    "watchProgress": current,
                    "replayed": True,
                }
            same_stream = bool(streamed and row and row["stream_id"] == stream)
            if same_stream and sequence <= row["stream_sequence"]:
                raise ApiError(409, "더 최신 시청 위치가 이미 저장됐습니다.")
            if identified and (
                not aid
                or (
                    data["watchVersion"] != current["version"]
                    and not (same_stream and data["watchVersion"] < current["version"])
                )
            ):
                raise ApiError(
                    409,
                    "다른 창에서 시청 위치를 변경했습니다. 최신 위치를 확인해주세요.",
                )
            duration = current["durationSeconds"]
            if duration and seconds > duration + 1:
                raise ApiError(400, "영상 길이를 넘는 재생 위치입니다.")
            saved = min(seconds, duration) if duration else seconds
            at = now_iso()
            c.execute(
                "INSERT INTO lesson_watch VALUES(?,?,?,?,?) ON CONFLICT(user_id,course_id,lesson_id) DO UPDATE SET seconds=excluded.seconds,updated_at=excluded.updated_at",
                (user["id"], cid, lid, saved, at),
            )
            c.execute(
                "INSERT INTO lesson_watch_sources VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,course_id,lesson_id) DO UPDATE SET asset_id=excluded.asset_id,lesson_version=excluded.lesson_version,updated_at=excluded.updated_at",
                (user["id"], cid, lid, aid, lesson["version"], at),
            )
            if aid:
                c.execute(
                    "INSERT INTO lesson_video_progress(user_id,course_id,lesson_id,asset_id,seconds,furthest_seconds,version,lesson_version,last_request_key,last_request_hash,stream_id,stream_sequence,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,course_id,lesson_id,asset_id) DO UPDATE SET seconds=excluded.seconds,furthest_seconds=excluded.furthest_seconds,version=excluded.version,lesson_version=excluded.lesson_version,last_request_key=excluded.last_request_key,last_request_hash=excluded.last_request_hash,stream_id=excluded.stream_id,stream_sequence=excluded.stream_sequence,updated_at=excluded.updated_at",
                    args
                    + (
                        saved,
                        max(saved, current["furthestSeconds"]),
                        current["version"] + 1,
                        lesson["version"],
                        key if identified else None,
                        request_hash,
                        stream if streamed else None,
                        sequence if streamed else None,
                        at,
                    ),
                )
            return {
                "seconds": saved,
                "watchProgress": self.state(c, user, course, lesson),
                "replayed": False,
            }

    def member_course(self, c, user, cid):
        course = c.execute("SELECT * FROM courses WHERE id=?", (cid,)).fetchone()
        if not course:
            return []
        items = []
        for lesson in c.execute(
            "SELECT * FROM course_lessons WHERE course_id=? AND status='published' AND video_asset_id IS NOT NULL",
            (cid,),
        ).fetchall():
            state = self.state(c, user, course, lesson)
            if state["updatedAt"]:
                items.append(
                    {
                        **state,
                        "lessonId": lesson["id"],
                        "lessonTitle": lesson["title"],
                        "lessonVersion": lesson["version"],
                    }
                )
        return sorted(
            items, key=lambda item: (item["updatedAt"], item["lessonId"]), reverse=True
        )
