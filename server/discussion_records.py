"""Immutable discussion content facts, shared by migration and live creation.

The question's existing version also tracks replies/resolution. Content versions
are independent; a migrated original is a baseline, never an invented edit.
"""

import hashlib
import json
import sqlite3
from .core import new_id, now_iso


KINDS = {"question": "questions", "answer": "answers"}
TABLES = {
    "question_revisions",
    "answer_revisions",
    "question_content_heads",
    "answer_content_heads",
    "question_attachments",
    "question_attachment_states",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def content(kind, row, attachments=()):
    if kind == "question":
        return {
            "title": row["title"],
            "body": row["body"],
            "visibility": row["visibility"],
            "attachments": sorted(attachments),
        }
    return {
        "body": row["body"],
        "official": bool(row["official"]),
        "attachments": sorted(attachments),
    }


def initialize(c, kind, row, origin, actor_id=None):
    if kind not in KINDS or origin not in ("baseline", "created"):
        raise ValueError("Invalid discussion baseline")
    snapshot = content(kind, row)
    rid, timestamp = new_id("qarevision"), now_iso()
    parent = row["id"] if kind == "question" else row["question_id"]
    c.execute(
        f"INSERT INTO {kind}_revisions(id,target_id,question_id,version,expected_version,origin,actor_id,reason,before_snapshot,after_snapshot,before_sha256,after_sha256,attachment_changes,recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            rid,
            row["id"],
            parent,
            1,
            0,
            origin,
            actor_id,
            None,
            None,
            canonical(snapshot),
            None,
            digest(snapshot),
            canonical({"add": [], "detach": []}),
            timestamp,
        ),
    )
    c.execute(
        f"INSERT INTO {kind}_content_heads(target_id,question_id,revision_id,version) VALUES(?,?,?,1)",
        (row["id"], parent, rid),
    )


def backfill(c):
    # Run inside the migration's write transaction. No old actor/reason/edit time
    # was collected, so only the imported current body and collection time exist.
    previous = c.row_factory
    c.row_factory = sqlite3.Row
    try:
        for kind, table in KINDS.items():
            for row in c.execute(f"SELECT * FROM {table} ORDER BY id").fetchall():
                initialize(c, kind, row, "baseline")
    finally:
        c.row_factory = previous


def metadata(c, kind, target_id):
    head = c.execute(
        f"SELECT h.version,r.origin FROM {kind}_content_heads h JOIN {kind}_revisions r ON r.id=h.revision_id WHERE h.target_id=?",
        (target_id,),
    ).fetchone()
    if not head:
        raise RuntimeError("Discussion content baseline is missing")
    return {"contentVersion": head["version"], "contentOrigin": head["origin"]}
