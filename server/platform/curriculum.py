"""Atomic curriculum proposals and idempotent batches of unpublished lessons."""

import json
import re
from ..core import ApiError, now_iso, new_id, text_field, choice_field
from .common import audit, notify, page_args, version
from .history import record_history
from .pricing import managed, encode, digest


def current(c, course):
    lessons = c.execute(
        "SELECT id,title,duration,status,version,section_id,sort_order FROM course_lessons WHERE course_id=? ORDER BY sort_order,id",
        (course["id"],),
    ).fetchall()
    sections = c.execute(
        "SELECT * FROM course_sections WHERE course_id=? ORDER BY sort_order,id",
        (course["id"],),
    ).fetchall()
    return {
        "courseId": course["id"],
        "courseTitle": course["title"],
        "courseVersion": course["version"],
        "status": course["status"],
        "layout": {
            "sections": [
                {
                    "id": s["id"],
                    "title": s["title"],
                    "lessons": [l["id"] for l in lessons if l["section_id"] == s["id"]],
                }
                for s in sections
            ]
        },
        "lessons": [
            {
                "id": l["id"],
                "title": l["title"],
                "duration": l["duration"],
                "status": l["status"],
                "version": l["version"],
            }
            for l in lessons
        ],
    }


def normalize(c, cid, value):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("sections"), list)
        or not 1 <= len(value["sections"]) <= 100
    ):
        raise ApiError(400, "모든 섹션을 포함한 커리큘럼 구성을 확인해주세요.")
    actual_sections = {
        r[0]
        for r in c.execute("SELECT id FROM course_sections WHERE course_id=?", (cid,))
    }
    actual_lessons = {
        r[0]
        for r in c.execute("SELECT id FROM course_lessons WHERE course_id=?", (cid,))
    }
    if len(actual_lessons) > 500:
        raise ApiError(
            409,
            "한 번에 편집할 수 있는 차시 수를 초과했습니다. 담당자에게 문의해주세요.",
        )
    sections, seen_sections, seen_lessons = [], set(), set()
    for item in value["sections"]:
        if not isinstance(item, dict):
            raise ApiError(400, "섹션 구성을 확인해주세요.")
        sid = text_field(item, "id", maximum=100)
        if sid not in actual_sections or sid in seen_sections:
            raise ApiError(400, "이 강의의 섹션을 빠짐없이 한 번씩 포함해주세요.")
        ids = item.get("lessons")
        if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
            raise ApiError(400, "차시 순서 목록을 확인해주세요.")
        for lid in ids:
            if lid not in actual_lessons or lid in seen_lessons:
                raise ApiError(400, "이 강의의 차시를 빠짐없이 한 번씩 포함해주세요.")
            seen_lessons.add(lid)
        seen_sections.add(sid)
        sections.append(
            {
                "id": sid,
                "title": text_field(item, "title", minimum=2, maximum=120),
                "lessons": ids,
            }
        )
    if seen_sections != actual_sections or seen_lessons != actual_lessons:
        raise ApiError(
            409,
            "새 차시/섹션 또는 누락 항목이 있습니다. 현재 구성을 다시 비교해주세요.",
        )
    return {"sections": sections}


class CurriculumService:
    def __init__(self, app):
        self.app = app

    def event(self, c, user, rid, action, reason, details=None):
        c.execute(
            "INSERT INTO curriculum_events VALUES(?,?,?,?,?,?,?)",
            (
                new_id("curriculumevent"),
                rid,
                user["id"],
                action,
                reason,
                encode(details or {}),
                now_iso(),
            ),
        )
        audit(c, user, "curriculum." + action, "curriculum", rid)

    def view(self, c, row):
        course = c.execute(
            "SELECT * FROM courses WHERE id=?", (row["course_id"],)
        ).fetchone()
        return {
            "id": row["id"],
            "courseId": row["course_id"],
            "authorId": row["author_id"],
            "status": row["status"],
            "baseVersion": row["base_version"],
            "base": json.loads(row["base_snapshot"]),
            "payload": json.loads(row["payload"]),
            "reason": row["reason"],
            "reviewNote": row["review_note"],
            "version": row["version"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "appliedVersion": row["applied_version"],
            "stale": row["status"] in ("draft", "submitted", "rejected")
            and row["base_version"] != course["version"],
        }

    def listing(self, user, cid, query):
        page, size, offset = page_args(query)
        with self.app.db.connect() as c:
            _, course = managed(c, user, cid)
            rows = c.execute(
                "SELECT * FROM curriculum_changes WHERE course_id=? ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (cid, size, offset),
            ).fetchall()
            return {
                "current": current(c, course),
                "changes": [self.view(c, r) for r in rows],
                "page": page,
                "pageSize": size,
                "total": c.execute(
                    "SELECT COUNT(*) FROM curriculum_changes WHERE course_id=?", (cid,)
                ).fetchone()[0],
            }

    def exact(self, user, cid, rid):
        with self.app.db.connect() as c:
            _, course = managed(c, user, cid)
            row = c.execute(
                "SELECT * FROM curriculum_changes WHERE id=? AND course_id=?",
                (rid, cid),
            ).fetchone()
            if not row:
                raise ApiError(404, "커리큘럼 변경안을 찾을 수 없습니다.")
            return {
                "change": self.view(c, row),
                "current": current(c, course),
                "events": [
                    {
                        "action": e["action"],
                        "reason": e["reason"],
                        "details": json.loads(e["details"]),
                        "createdAt": e["created_at"],
                    }
                    for e in c.execute(
                        "SELECT * FROM curriculum_events WHERE change_id=? ORDER BY rowid",
                        (rid,),
                    )
                ],
            }

    def new(self, user, cid):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, course = managed(c, user, cid)
            row = c.execute(
                "SELECT * FROM curriculum_changes WHERE course_id=? AND status IN ('draft','submitted','rejected')",
                (cid,),
            ).fetchone()
            if row:
                if row["author_id"] != user["id"] and user["role"] != "admin":
                    raise ApiError(
                        409, "다른 작성자의 구성 변경안은 관리자가 확인해야 합니다."
                    )
                return {"change": self.view(c, row)}
            rid, at = new_id("curriculum"), now_iso()
            base = current(c, course)
            c.execute(
                "INSERT INTO curriculum_changes(id,course_id,author_id,base_version,base_snapshot,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    rid,
                    cid,
                    user["id"],
                    course["version"],
                    encode(base),
                    encode(base["layout"]),
                    at,
                    at,
                ),
            )
            self.event(
                c, user, rid, "created", "현재 구성을 기준으로 변경안을 만들었습니다."
            )
            return {
                "change": self.view(
                    c,
                    c.execute(
                        "SELECT * FROM curriculum_changes WHERE id=?", (rid,)
                    ).fetchone(),
                )
            }

    def change(self, user, cid, rid, data, save=False):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, course = managed(c, user, cid)
            row = c.execute(
                "SELECT * FROM curriculum_changes WHERE id=? AND course_id=?",
                (rid, cid),
            ).fetchone()
            if not row:
                raise ApiError(404, "커리큘럼 변경안을 찾을 수 없습니다.")
            version(data, row)
            action = (
                "save"
                if save
                else choice_field(
                    data,
                    "action",
                    (
                        "submit",
                        "withdraw",
                        "reject",
                        "approve",
                        "apply",
                        "rebase",
                        "cancel",
                    ),
                )
            )
            if action in ("approve", "reject") and user["role"] != "admin":
                raise ApiError(403, "구성 검수는 관리자만 처리할 수 있습니다.")
            if row["author_id"] != user["id"] and user["role"] != "admin":
                raise ApiError(403, "본인이 작성한 구성 변경안만 수정할 수 있습니다.")
            allowed = {
                "save": ("draft", "rejected"),
                "submit": ("draft", "rejected"),
                "withdraw": ("submitted",),
                "reject": ("submitted",),
                "approve": ("submitted",),
                "apply": ("draft", "rejected"),
                "rebase": ("draft", "rejected"),
                "cancel": ("draft", "submitted", "rejected"),
            }
            if row["status"] not in allowed[action]:
                raise ApiError(409, "현재 변경안 상태에서는 처리할 수 없습니다.")
            if (
                action in ("save", "submit", "apply", "approve")
                and row["base_version"] != course["version"]
            ):
                raise ApiError(
                    409, "상품/차시가 바뀌었습니다. 현재 구성을 다시 비교해주세요."
                )
            reason = text_field(
                data, "reason", minimum=0 if save else 5, maximum=2000, optional=save
            )
            at = now_iso()
            if action in ("save", "rebase"):
                payload = normalize(c, cid, data.get("payload"))
                if action == "rebase":
                    if (
                        type(data.get("courseVersion")) is not int
                        or data["courseVersion"] != course["version"]
                    ):
                        raise ApiError(
                            409, "비교한 상품 버전이 달라졌습니다. 다시 확인해주세요."
                        )
                    base = current(c, course)
                    c.execute(
                        "UPDATE curriculum_changes SET base_version=?,base_snapshot=? WHERE id=?",
                        (course["version"], encode(base), rid),
                    )
                c.execute(
                    "UPDATE curriculum_changes SET status='draft',payload=?,reason=?,version=version+1,updated_at=? WHERE id=?",
                    (encode(payload), reason, at, rid),
                )
            elif action in ("apply", "approve", "submit"):
                payload = normalize(c, cid, json.loads(row["payload"]))
                if payload == current(c, course)["layout"]:
                    raise ApiError(
                        409, "현재 구성과 같습니다. 변경한 구성을 먼저 저장해주세요."
                    )
                if action == "submit":
                    c.execute(
                        "UPDATE curriculum_changes SET status='submitted',reason=?,version=version+1,updated_at=? WHERE id=?",
                        (reason, at, rid),
                    )
                else:
                    if action == "apply" and course["status"] not in (
                        "draft",
                        "review",
                    ):
                        raise ApiError(
                            403,
                            "공개/보관 강의의 구성은 관리자 검수 요청 후 승인해야 합니다.",
                        )
                    changed = self.apply(c, user, course, payload, rid, reason)
                    c.execute(
                        "UPDATE curriculum_changes SET status='applied',review_note=?,reviewer_id=?,applied_version=?,version=version+1,updated_at=? WHERE id=?",
                        (reason, user["id"], course["version"] + 1, at, rid),
                    )
                    notify(
                        c,
                        course["instructor_user_id"],
                        "커리큘럼 구성이 반영되었습니다.",
                        "차시 ID와 기존 학습 기록을 유지했습니다. 상품 제공 조건도 확인해주세요.",
                        "#/studio/course/" + cid + "/curriculum?change=" + rid,
                        event_key="curriculum.applied:" + rid,
                        category="operation",
                        reference_kind="course_staff",
                        reference_id=cid,
                        group_key="course:" + cid,
                    )
            else:
                state = {
                    "withdraw": "draft",
                    "reject": "rejected",
                    "cancel": "cancelled",
                }[action]
                c.execute(
                    "UPDATE curriculum_changes SET status=?,review_note=?,version=version+1,updated_at=? WHERE id=?",
                    (state, reason, at, rid),
                )
            self.event(
                c,
                user,
                rid,
                action,
                reason,
                {
                    "baseCourseVersion": row["base_version"],
                    "courseVersion": course["version"]
                    + (1 if action in ("apply", "approve") else 0),
                },
            )
            return {
                "change": self.view(
                    c,
                    c.execute(
                        "SELECT * FROM curriculum_changes WHERE id=?", (rid,)
                    ).fetchone(),
                )
            }

    def apply(self, c, user, course, payload, rid, reason):
        index, changed = 0, 0
        at = now_iso()
        for order, section in enumerate(payload["sections"]):
            c.execute(
                "UPDATE course_sections SET title=?,sort_order=? WHERE id=? AND course_id=?",
                (section["title"], order, section["id"], course["id"]),
            )
            for lid in section["lessons"]:
                row = c.execute(
                    "SELECT * FROM course_lessons WHERE course_id=? AND id=?",
                    (course["id"], lid),
                ).fetchone()
                if row["section_id"] != section["id"] or row["sort_order"] != index:
                    record_history(c, row)
                    c.execute(
                        "UPDATE course_lessons SET section_id=?,sort_order=?,version=version+1,updated_at=? WHERE course_id=? AND id=?",
                        (section["id"], index, at, course["id"], lid),
                    )
                    updated = c.execute(
                        "SELECT * FROM course_lessons WHERE course_id=? AND id=?",
                        (course["id"], lid),
                    ).fetchone()
                    record_history(
                        c, updated, user, "direct", "커리큘럼 " + rid + " · " + reason
                    )
                    changed += 1
                index += 1
        c.execute(
            "UPDATE courses SET version=version+1,updated_at=? WHERE id=?",
            (at, course["id"]),
        )
        return changed

    def batch(self, user, cid, data):
        request_key = text_field(data, "requestKey", minimum=8, maximum=80)
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", request_key):
            raise ApiError(400, "초안 생성 요청 식별자를 확인해주세요.")
        items = data.get("lessons")
        if not isinstance(items, list) or not 1 <= len(items) <= 50:
            raise ApiError(400, "한 번에 1~50개 차시 초안을 만들어주세요.")
        entries = []
        for item in items:
            if not isinstance(item, dict):
                raise ApiError(400, "차시 제목/시간을 확인해주세요.")
            entries.append(
                {
                    "title": text_field(item, "title", minimum=2, maximum=120),
                    "duration": text_field(item, "duration", maximum=40, optional=True),
                }
            )
        if len({e["title"] for e in entries}) != len(entries):
            raise ApiError(400, "같은 묶음의 차시 제목이 중복됩니다.")
        sid = text_field(data, "sectionId", maximum=100)
        body_hash = digest({"sectionId": sid, "lessons": entries})
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, course = managed(c, user, cid)
            previous = c.execute(
                "SELECT * FROM lesson_batches WHERE course_id=? AND author_id=? AND request_key=?",
                (cid, user["id"], request_key),
            ).fetchone()
            if previous:
                if body_hash != previous["payload_hash"]:
                    raise ApiError(
                        409,
                        "이미 처리한 요청의 작성 내용이 다릅니다. 새 요청으로 확인해주세요.",
                    )
                return {
                    "batchId": previous["id"],
                    "lessonIds": json.loads(previous["lesson_ids"]),
                    "replayed": True,
                }
            version(data, course)
            if (
                course["status"] in ("published", "archived")
                and user["role"] != "admin"
            ):
                raise ApiError(
                    403,
                    "공개 강의의 신규 차시는 관리자가 검수해야 합니다. 초안 강의에서 작성해주세요.",
                )
            if not c.execute(
                "SELECT 1 FROM course_sections WHERE id=? AND course_id=?", (sid, cid)
            ).fetchone():
                raise ApiError(400, "이 강의의 섹션을 선택해주세요.")
            count = c.execute(
                "SELECT COUNT(*) FROM course_lessons WHERE course_id=?", (cid,)
            ).fetchone()[0]
            if count + len(entries) > 500:
                raise ApiError(409, "강의의 차시 초안은 500개 이내로 구성해주세요.")
            order = c.execute(
                "SELECT COALESCE(MAX(sort_order),-1)+1 FROM course_lessons WHERE course_id=?",
                (cid,),
            ).fetchone()[0]
            at, ids = now_iso(), []
            for n, entry in enumerate(entries):
                lid = new_id("lesson").replace("_", "-")
                c.execute(
                    "INSERT INTO course_lessons(course_id,id,section_id,title,duration,status,sort_order,created_at,updated_at) VALUES(?,?,?,?,?,'draft',?,?,?)",
                    (
                        cid,
                        lid,
                        sid,
                        entry["title"],
                        entry["duration"],
                        order + n,
                        at,
                        at,
                    ),
                )
                record_history(
                    c,
                    c.execute(
                        "SELECT * FROM course_lessons WHERE course_id=? AND id=?",
                        (cid, lid),
                    ).fetchone(),
                    user,
                    "direct",
                    "차시 초안 묶음 생성",
                )
                ids.append(lid)
            c.execute(
                "UPDATE courses SET version=version+1,updated_at=? WHERE id=?",
                (at, cid),
            )
            bid = new_id("lessonbatch")
            c.execute(
                "INSERT INTO lesson_batches VALUES(?,?,?,?,?,?,?)",
                (bid, cid, user["id"], request_key, body_hash, encode(ids), at),
            )
            audit(
                c,
                user,
                "lessons.batch_created",
                "course",
                cid,
                {"batchId": bid, "lessonIds": ids},
            )
            return {"batchId": bid, "lessonIds": ids, "replayed": False}
