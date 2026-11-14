"""Explicit, local-only installation of an owned sample on a free pilot lesson.

The host operator is the actor; no account or persistent role is created. The
normal content validator, version guard, snapshots and audit remain in use.
"""

import hashlib
import os
from pathlib import Path

from .core import ApiError, new_id, now_iso
from .media_metadata import mp4_duration
from .platform.common import audit
from .platform.history import editable_snapshot


def install_sample(
    app, source, course_id, lesson_id, expected_version, expected_course_version, reason
):
    source = Path(source).resolve()
    if not source.is_file() or not 32 <= source.stat().st_size <= 100 * 1024 * 1024:
        raise ApiError(400, "32바이트~100MiB의 샘플 MP4를 선택해주세요.")
    aid = new_id("asset")
    directory = app.platform.content.media_dir
    temporary = directory / (aid + ".importing")
    target = directory / (aid + ".mp4")
    linked = False
    try:
        sha = hashlib.sha256()
        size = 0
        with source.open("rb") as src, os.fdopen(
            os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
        ) as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise ApiError(400, "샘플 영상이 너무 큽니다.")
                sha.update(chunk)
                dst.write(chunk)
        duration = mp4_duration(temporary)
        if not duration:
            raise ApiError(400, "재생 길이를 확인할 수 있는 MP4가 필요합니다.")
        checksum = sha.hexdigest()
        with app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            course = c.execute(
                "SELECT * FROM courses WHERE id=?", (course_id,)
            ).fetchone()
            lesson = c.execute(
                "SELECT * FROM course_lessons WHERE course_id=? AND id=?",
                (course_id, lesson_id),
            ).fetchone()
            if (
                not course
                or not lesson
                or course["status"] != "published"
                or lesson["status"] != "published"
                or not course["pilot"]
                or course["price"] != 0
                or not lesson["free"]
            ):
                raise ApiError(
                    409, "공개 무료 파일럿의 무료 차시에만 샘플을 연결합니다."
                )
            if lesson["video_asset_id"]:
                current = c.execute(
                    "SELECT a.id,a.sha256,m.is_sample,m.duration_seconds FROM media_assets a LEFT JOIN media_playback_metadata m ON m.asset_id=a.id WHERE a.id=? AND a.course_id=?",
                    (lesson["video_asset_id"], course_id),
                ).fetchone()
                if current and current["sha256"] == checksum and current["is_sample"]:
                    return {
                        "changed": False,
                        "assetId": current["id"],
                        "lessonId": lesson_id,
                        "lessonVersion": lesson["version"],
                        "courseVersion": course["version"],
                        "durationSeconds": current["duration_seconds"],
                    }
                raise ApiError(
                    409,
                    "이미 연결된 영상은 덮어쓰지 않습니다. 운영 검수를 통해 교체해주세요.",
                )
            if (
                type(expected_version) is not int
                or lesson["version"] != expected_version
                or type(expected_course_version) is not int
                or course["version"] != expected_course_version
            ):
                raise ApiError(
                    409, "강의/차시 버전이 변경됐습니다. 현재 상태를 확인해주세요."
                )
            if c.execute(
                "SELECT 1 FROM lesson_revisions WHERE course_id=? AND lesson_id=? AND status IN ('draft','submitted')",
                (course_id, lesson_id),
            ).fetchone():
                raise ApiError(409, "진행 중인 차시 변경안을 먼저 확인해주세요.")
            os.link(temporary, target)
            linked = True
            c.execute(
                "INSERT INTO media_assets(id,course_id,uploader_id,filename,size,sha256,created_at) VALUES(?,?,NULL,?,?,?,?)",
                (aid, course_id, "TPC-프리플랍-샘플.mp4", size, checksum, now_iso()),
            )
            asset = c.execute(
                "SELECT * FROM media_assets WHERE id=?", (aid,)
            ).fetchone()
            meta = app.platform.watch_progress.metadata(
                c, asset, cache=True, sample=True
            )
            data = editable_snapshot(lesson)
            data.update(
                version=lesson["version"],
                videoAssetId=aid,
                duration=f"샘플 영상 {int(duration)//60}:{int(duration)%60:02d}",
            )
            system = {"id": None, "role": "admin"}
            app.platform.content.write_lesson(
                c, system, course_id, data, lesson_id, source="direct", reason=reason
            )
            audit(
                c,
                None,
                "sample.installed",
                "lesson",
                course_id + "/" + lesson_id,
                {
                    "assetId": aid,
                    "sha256": checksum,
                    "durationSeconds": duration,
                    "reason": reason,
                    "actor": "local-operator",
                },
            )
            row = c.execute(
                "SELECT version FROM course_lessons WHERE course_id=? AND id=?",
                (course_id, lesson_id),
            ).fetchone()
            parent = c.execute(
                "SELECT version FROM courses WHERE id=?", (course_id,)
            ).fetchone()
            return {
                "changed": True,
                "assetId": aid,
                "lessonId": lesson_id,
                "lessonVersion": row[0],
                "courseVersion": parent[0],
                **meta,
            }
    except Exception:
        if linked:
            target.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
