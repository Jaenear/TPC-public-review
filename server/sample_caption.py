"""Local installation of captions for the one owned development sample.

No account or role is created. This technical sample review is distinct from an
administrator accepting real coach content through the protected review API.
"""

import hashlib
from pathlib import Path

from .core import ApiError
from .platform.captions import JOIN, digest
from .webvtt import validate, MAX_BYTES

OWNED_SAMPLE_SHA = "1d2cf29f94ecab4cdf0f1af93f28515b6da7f5c2e2a4f82f397d46ef9535dc39"


def install_sample_caption(app, source, course_id, lesson_id, expected_sha, reason):
    source = Path(source)
    if (
        source.is_symlink()
        or not source.is_file()
        or not 8 <= source.stat().st_size <= MAX_BYTES
    ):
        raise ApiError(400, "1MiB 이내의 자체 샘플 .vtt 원본이 필요합니다.")
    if source.suffix.lower() != ".vtt" or not 5 <= len(reason.strip()) <= 500:
        raise ApiError(400, "샘플 자막 파일과 5~500자 설치 사유를 확인해주세요.")
    body = source.read_bytes()
    parsed = validate(body)
    service = app.platform.captions
    actor = {"id": None}
    checksum = hashlib.sha256(body).hexdigest()
    with app.db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        course = c.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
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
                409, "공개 무료 파일럿의 무료 차시에만 자체 샘플 자막을 연결합니다."
            )
        asset = service.asset(c, course_id, lesson["video_asset_id"])
        meta = app.platform.watch_progress.metadata(c, asset)
        if (
            expected_sha != OWNED_SAMPLE_SHA
            or asset["sha256"] != OWNED_SAMPLE_SHA
            or not meta["isSample"]
        ):
            raise ApiError(
                409, "허용된 자체 제작 샘플 원본과 현재 연결을 확인해주세요."
            )
        old = c.execute(
            JOIN
            + "WHERE a.media_asset_id=? AND a.uploader_id IS NULL AND a.language='ko' AND a.kind='subtitles' AND a.sha256=?",
            (asset["id"], checksum),
        ).fetchone()
        if old:
            if old["status"] != "approved":
                raise ApiError(
                    409,
                    "이미 처리한 샘플 자막은 다시 승인하지 않습니다. 운영 검수를 이용해주세요.",
                )
            return {"changed": False, "caption": service.view(old, True)}
        if service.active(c, asset["id"], "ko", "subtitles"):
            raise ApiError(409, "현재 승인된 한국어 자막을 덮어쓰지 않습니다.")
        service.check_capacity(c, asset["id"])
        data = service.metadata(
            {
                "filename": source.name,
                "language": "ko",
                "label": "한국어 · 자체 제작 샘플",
                "kind": "subtitles",
                "requestKey": "sample_caption_" + checksum,
                "sha256": checksum,
                "mediaSha256": expected_sha,
            }
        )
        result = service.create(
            c, actor, course_id, asset, data, body, parsed, digest(data)
        )
        row = service.get(c, course_id, result["caption"]["id"])
        service.verify_original(c, row)
        service.decide(
            c,
            actor,
            row,
            "approve",
            reason.strip(),
            "sample_review_" + checksum,
            digest({"sample": checksum, "reason": reason.strip()}),
            "자체 제작 개발 샘플 · 로컬 기술 확인, 실제 코치 강의 운영 인수와 별도",
            sample=True,
        )
        return {
            "changed": True,
            "caption": service.view(service.get(c, course_id, row["id"]), True),
        }
