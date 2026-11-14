"""Immutable snapshots of lesson versions; private to course operators."""

import json
from ..core import new_id, now_iso
from .common import row_dict

EDITABLE_FIELDS = (
    "title",
    "duration",
    "sectionId",
    "sortOrder",
    "status",
    "free",
    "content",
    "takeaway",
    "quiz",
    "videoAssetId",
    "resources",
)


def lesson_snapshot(row):
    item = row_dict(row)
    item["content"] = json.loads(row["content"])
    item["quiz"] = json.loads(row["quiz"]) if row["quiz"] else None
    item["free"] = bool(row["free"])
    item["resources"] = (
        json.loads(row["resources"]) if "resources" in row.keys() else []
    )
    return item


def editable_snapshot(row):
    item = lesson_snapshot(row)
    return {key: item[key] for key in EDITABLE_FIELDS}


def record_history(
    connection, row, user=None, source="baseline", reason="", revision_id=None
):
    connection.execute(
        "INSERT OR IGNORE INTO lesson_history(id,course_id,lesson_id,lesson_version,snapshot,actor_id,source,revision_id,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            new_id("history"),
            row["course_id"],
            row["id"],
            row["version"],
            json.dumps(lesson_snapshot(row), ensure_ascii=False),
            user["id"] if user else None,
            source,
            revision_id,
            reason,
            row["updated_at"] if source == "baseline" else now_iso(),
        ),
    )
