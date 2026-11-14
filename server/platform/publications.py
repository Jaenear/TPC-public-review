"""Version-bound course quality review. No automatic commercial/rights approval."""

import hashlib
import json
from ..core import ApiError, new_id, now_iso, text_field, choice_field
from .common import audit, notify, page_args, version
from .history import lesson_snapshot
from .pricing import managed, encode, digest
from .contracts import current_actor, qualified

CHECKLIST_VERSION = "TPC-QUALITY-1"
CHECKS = (
    {
        "id": "objectives",
        "title": "학습 목표와 대상",
        "description": "소개·난이도와 차시별 목표가 일치하는지 확인하고 확인한 위치를 남깁니다.",
    },
    {
        "id": "content",
        "title": "본문·설명·확인 문제",
        "description": "공개할 차시의 본문/영상·핵심 정리·퀴즈 정답과 설명을 검토합니다.",
    },
    {
        "id": "preview",
        "title": "미리보기와 수강 범위",
        "description": "무료 미리보기·보호 차시의 구분과 수강생 동선을 확인합니다.",
    },
    {
        "id": "assets",
        "title": "영상·학습 자료 연결",
        "description": "연결 원본과 차시의 제공 상태를 확인합니다. 자료가 없으면 텍스트 제공 범위를 명시합니다.",
    },
    {
        "id": "rights",
        "title": "공개 동의와 출처",
        "description": "원본/편집본·미리보기·참고 자료의 제공 범위와 확인 근거를 기록합니다. 실제 권리 인수는 별도입니다.",
    },
)
CHECK_IDS = {item["id"] for item in CHECKS}
FINAL = ("changes_requested", "published", "withdrawn")


def file_matches(path, asset, maximum, full=False):
    """Fail closed on unavailable originals without returning private paths."""
    try:
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != asset["size"]
        ):
            return False
        if not full:
            return True
        hasher, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
                size += len(chunk)
                if size > maximum:
                    return False
        return size == asset["size"] and hasher.hexdigest() == asset["sha256"]
    except OSError:
        return False


def normalize_checks(data, reviewer=False, complete=False):
    if not isinstance(data, dict) or set(data) - CHECK_IDS:
        raise ApiError(400, "정해진 품질 체크 항목을 사용해주세요.")
    result = {}
    for item in CHECKS:
        key = item["id"]
        value = data.get(key, {})
        if not isinstance(value, dict) or set(value) - {"state", "note"}:
            raise ApiError(400, "체크 상태와 확인 근거를 입력해주세요.")
        state = value.get("state", "pending")
        allowed = (
            ("pending", "pass", "changes") if reviewer else ("pending", "confirmed")
        )
        if state not in allowed:
            raise ApiError(400, "품질 확인 상태를 확인해주세요.")
        note = text_field(value, "note", maximum=2000, optional=True)
        if state != "pending" and len(note) < 5:
            raise ApiError(
                400, item["title"] + ": 확인한 위치와 근거를 5자 이상 남겨주세요."
            )
        if complete and state != ("pass" if reviewer else "confirmed"):
            raise ApiError(409, item["title"] + ": 모든 항목의 확인이 필요합니다.")
        result[key] = {"state": state, "note": note}
    return result


def snapshot(c, course):
    sections = [
        dict(row)
        for row in c.execute(
            "SELECT * FROM course_sections WHERE course_id=? ORDER BY sort_order,id",
            (course["id"],),
        )
    ]
    lessons = [
        lesson_snapshot(row)
        for row in c.execute(
            "SELECT l.* FROM course_lessons l JOIN course_sections s ON s.id=l.section_id AND s.course_id=l.course_id WHERE l.course_id=? ORDER BY s.sort_order,s.id,l.sort_order,l.id",
            (course["id"],),
        )
    ]
    resources, videos = {}, {}
    for lesson in lessons:
        for ref in lesson["resources"]:
            row = c.execute(
                "SELECT id,course_id,filename,mime_type,size,sha256,status,version FROM learning_resources WHERE id=?",
                (ref["assetId"],),
            ).fetchone()
            resources[ref["assetId"]] = dict(row) if row else None
        if lesson["videoAssetId"]:
            row = c.execute(
                "SELECT id,course_id,filename,size,sha256 FROM media_assets WHERE id=?",
                (lesson["videoAssetId"],),
            ).fetchone()
            videos[lesson["videoAssetId"]] = dict(row) if row else None
    return {
        "course": {
            key: course[key]
            for key in (
                "id",
                "title",
                "subtitle",
                "description",
                "category",
                "level",
                "price",
                "duration",
                "pilot",
                "instructor_user_id",
                "status",
                "version",
            )
        },
        "instructorEligible": qualified(c, course["instructor_user_id"]),
        "sections": sections,
        "lessons": lessons,
        "resources": resources,
        "videos": videos,
    }


class PublicationService:
    def __init__(self, app):
        self.app = app

    def current(self, c, course, verify_files=False):
        basis = snapshot(c, course)
        published = [
            lesson for lesson in basis["lessons"] if lesson["status"] == "published"
        ]
        blockers = []
        if not published:
            blockers.append("공개할 차시가 한 개 이상 필요합니다.")
        if not course["pilot"] and not basis["instructorEligible"]:
            blockers.append("정식 강의에는 현재 활성 담당 코치가 필요합니다.")
        for lesson in published:
            row = c.execute(
                "SELECT * FROM course_lessons WHERE course_id=? AND id=?",
                (course["id"], lesson["id"]),
            ).fetchone()
            try:
                self.app.platform.content.prepare_lesson(c, course, lesson, row)
            except ApiError as error:
                blockers.append(lesson["title"] + ": " + error.message)
            files = [
                (
                    basis["resources"].get(ref["assetId"]),
                    self.app.platform.resources.directory / (ref["assetId"] + ".bin"),
                    10 * 1024 * 1024,
                )
                for ref in lesson["resources"]
            ]
            if lesson["videoAssetId"]:
                files.append(
                    (
                        basis["videos"].get(lesson["videoAssetId"]),
                        self.app.platform.content.media_dir
                        / (lesson["videoAssetId"] + ".mp4"),
                        100 * 1024 * 1024,
                    )
                )
            for asset, path, maximum in files:
                if (
                    asset is None
                    or asset["course_id"] != course["id"]
                    or not file_matches(path, asset, maximum, verify_files)
                ):
                    blockers.append(
                        lesson["title"]
                        + ": 연결 원본 파일의 존재/무결성을 확인할 수 없습니다."
                    )
        counts = {
            "lessons": len(basis["lessons"]),
            "published": len(published),
            "preview": sum(bool(l["free"]) for l in published),
            "protected": sum(not l["free"] for l in published),
            "draft": sum(l["status"] == "draft" for l in basis["lessons"]),
            "videos": len(basis["videos"]),
            "resources": len(basis["resources"]),
            "quizzes": sum(bool(l["quiz"]) for l in published),
        }
        return {
            "snapshot": basis,
            "hash": digest(basis),
            "courseVersion": course["version"],
            "counts": counts,
            "blockers": list(dict.fromkeys(blockers)),
            "ready": not blockers,
        }

    def view(self, c, row, current=None, include_snapshot=True):
        item = {
            "id": row["id"],
            "courseId": row["course_id"],
            "authorId": row["author_id"],
            "previousId": row["previous_id"],
            "status": row["status"],
            "checklistVersion": row["checklist_version"],
            "baseVersion": row["base_version"],
            "basisHash": row["snapshot_hash"],
            "version": row["version"],
            "authorNote": row["author_note"],
            "reviewNote": row["review_note"],
            "decisionReason": row["decision_reason"],
            "reviewerId": row["reviewer_id"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "submittedAt": row["submitted_at"],
            "decidedAt": row["decided_at"],
            "publishedVersion": row["published_version"],
            "authorChecks": normalize_checks(json.loads(row["author_checks"])),
            "reviewerChecks": normalize_checks(
                json.loads(row["reviewer_checks"]), True
            ),
        }
        if include_snapshot:
            item["snapshot"] = json.loads(row["snapshot"])
        item["currentChanged"] = bool(
            current and current["hash"] != row["snapshot_hash"]
        )
        item["stale"] = item["currentChanged"] and row["status"] in (
            "draft",
            "submitted",
        )
        return item

    def row(self, c, cid, rid):
        row = c.execute(
            "SELECT * FROM course_reviews WHERE id=? AND course_id=?", (rid, cid)
        ).fetchone()
        if not row:
            raise ApiError(404, "강의 검수 기록을 찾을 수 없습니다.")
        return row

    def event(self, c, user, rid, action, reason, details=None):
        c.execute(
            "INSERT INTO course_review_events VALUES(?,?,?,?,?,?,?)",
            (
                new_id("reviewevent"),
                rid,
                user["id"],
                action,
                reason,
                encode(details or {}),
                now_iso(),
            ),
        )
        audit(c, user, "publication." + action, "course_review", rid, details)

    def listing(self, user, cid, query):
        with self.app.db.connect() as c:
            user, course = managed(c, user, cid)
            page, limit, offset = page_args(query)
            current = self.current(c, course)
            rows = c.execute(
                "SELECT * FROM course_reviews WHERE course_id=? ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?",
                (cid, limit, offset),
            ).fetchall()
            return {
                "current": current,
                "checklist": CHECKS,
                "checklistVersion": CHECKLIST_VERSION,
                "reviews": [self.view(c, row, current, False) for row in rows],
                "page": page,
                "total": c.execute(
                    "SELECT COUNT(*) FROM course_reviews WHERE course_id=?", (cid,)
                ).fetchone()[0],
            }

    def queue(self, user, query):
        with self.app.db.connect() as c:
            user = current_actor(c, user)
            page, limit, offset = page_args(query)
            state = query.get("status", ["submitted"])[0]
            if state not in ("all", "draft", "submitted", *FINAL):
                raise ApiError(400, "검수 상태를 확인해주세요.")
            where, args = "WHERE 1=1", []
            if user["role"] != "admin":
                where += " AND c.instructor_user_id=?"
                args.append(user["id"])
            if state != "all":
                where += " AND r.status=?"
                args.append(state)
            sql = " FROM course_reviews r JOIN courses c ON c.id=r.course_id " + where
            rows = c.execute(
                "SELECT r.*"
                + sql
                + " ORDER BY r.created_at DESC,r.rowid DESC LIMIT ? OFFSET ?",
                args + [limit, offset],
            ).fetchall()
            reviews = []
            for row in rows:
                course = c.execute(
                    "SELECT * FROM courses WHERE id=?", (row["course_id"],)
                ).fetchone()
                item = self.view(c, row, self.current(c, course), False)
                item["courseTitle"] = course["title"]
                reviews.append(item)
            return {
                "reviews": reviews,
                "page": page,
                "total": c.execute("SELECT COUNT(*)" + sql, args).fetchone()[0],
            }

    def exact(self, user, cid, rid, query):
        with self.app.db.connect() as c:
            _, course = managed(c, user, cid)
            current = self.current(c, course)
            row = self.row(c, cid, rid)
            cp, limit, offset = page_args({"page": query.get("commentPage", ["1"])})
            comments = [
                dict(r)
                for r in c.execute(
                    "SELECT id,author_id,check_id,body,created_at FROM course_review_comments WHERE review_id=? ORDER BY created_at,rowid LIMIT ? OFFSET ?",
                    (rid, limit, offset),
                )
            ]
            ep, limit, offset = page_args({"page": query.get("eventPage", ["1"])})
            events = [
                {**dict(r), "details": json.loads(r["details"])}
                for r in c.execute(
                    "SELECT * FROM course_review_events WHERE review_id=? ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?",
                    (rid, limit, offset),
                )
            ]
            return {
                "review": self.view(c, row, current),
                "current": current,
                "checklist": CHECKS,
                "comments": comments,
                "commentPage": cp,
                "commentTotal": c.execute(
                    "SELECT COUNT(*) FROM course_review_comments WHERE review_id=?",
                    (rid,),
                ).fetchone()[0],
                "events": events,
                "eventPage": ep,
                "eventTotal": c.execute(
                    "SELECT COUNT(*) FROM course_review_events WHERE review_id=?",
                    (rid,),
                ).fetchone()[0],
            }

    def new(self, user, cid):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, course = managed(c, user, cid)
            if course["status"] == "published":
                raise ApiError(
                    409, "발행된 강의의 내용은 차시/구성 변경안에서 검수해주세요."
                )
            opened = c.execute(
                "SELECT * FROM course_reviews WHERE course_id=? AND status IN ('draft','submitted')",
                (cid,),
            ).fetchone()
            if opened:
                if opened["author_id"] != user["id"] and user["role"] != "admin":
                    raise ApiError(
                        409,
                        "이전 담당자의 검수가 진행 중입니다. 관리자에게 회수를 요청해주세요.",
                    )
                return {"review": self.view(c, opened, self.current(c, course))}
            previous = c.execute(
                "SELECT id FROM course_reviews WHERE course_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (cid,),
            ).fetchone()
            current = self.current(c, course)
            rid, timestamp = new_id("coursereview"), now_iso()
            c.execute(
                "INSERT INTO course_reviews(id,course_id,author_id,previous_id,checklist_version,base_version,snapshot,snapshot_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    rid,
                    cid,
                    user["id"],
                    previous["id"] if previous else None,
                    CHECKLIST_VERSION,
                    course["version"],
                    encode(current["snapshot"]),
                    current["hash"],
                    timestamp,
                    timestamp,
                ),
            )
            self.event(c, user, rid, "created", "강의 품질 검수 초안을 만들었습니다.")
            return {"review": self.view(c, self.row(c, cid, rid), current)}

    def change(self, user, cid, rid, data, save=False):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, course = managed(c, user, cid)
            old = self.row(c, cid, rid)
            version(data, old)
            if old["status"] in FINAL:
                raise ApiError(
                    409,
                    "마감한 검수 기록은 변경하지 않습니다. 보완 후 새 검수를 만들어주세요.",
                )
            admin, author = user["role"] == "admin", user["id"] == old["author_id"]
            action = (
                "save"
                if save
                else choice_field(
                    data,
                    "action",
                    ("submit", "publish", "reject", "withdraw", "rebase"),
                )
            )
            if action in ("submit", "rebase") or (save and old["status"] == "draft"):
                if not author:
                    raise ApiError(
                        403, "작성한 코치만 자체 점검을 저장/제출할 수 있습니다."
                    )
            elif action in ("publish", "reject") or (
                save and old["status"] == "submitted"
            ):
                if not admin:
                    raise ApiError(
                        403, "관리자만 항목별 검수와 발행을 처리할 수 있습니다."
                    )
            elif not (admin or author):
                raise ApiError(403, "검수 요청을 회수할 권한이 없습니다.")
            if course["status"] == "published":
                raise ApiError(409, "현재 강의는 이미 발행됐습니다.")
            current = self.current(
                c, course, verify_files=action in ("submit", "publish")
            )
            stale = current["hash"] != old["snapshot_hash"]
            state, base, basis, basis_hash = (
                old["status"],
                old["base_version"],
                old["snapshot"],
                old["snapshot_hash"],
            )
            author_checks, reviewer_checks = (
                old["author_checks"],
                old["reviewer_checks"],
            )
            author_note, review_note = old["author_note"], old["review_note"]
            submitted_at, decided_at, published_version = (
                old["submitted_at"],
                old["decided_at"],
                old["published_version"],
            )
            reviewer_id = old["reviewer_id"]
            decision_reason = old["decision_reason"]
            reason = (
                text_field(data, "reason", minimum=5, maximum=2000)
                if not save
                else "체크리스트와 확인 근거를 저장했습니다."
            )
            if save:
                if stale and state == "draft":
                    raise ApiError(
                        409,
                        "검수 대상이 바뀌었습니다. 현재 내용을 비교한 뒤 다시 점검해주세요.",
                    )
                if state == "draft":
                    author_checks = encode(normalize_checks(data.get("checks")))
                    author_note = text_field(data, "note", maximum=4000, optional=True)
                else:
                    reviewer_checks = encode(normalize_checks(data.get("checks"), True))
                    review_note = text_field(data, "note", maximum=4000, optional=True)
                    reviewer_id = user["id"]
            elif action == "rebase":
                if (
                    state != "draft"
                    or data.get("courseVersion") != current["courseVersion"]
                    or data.get("basisHash") != current["hash"]
                ):
                    raise ApiError(
                        409, "직접 비교한 현재 상품 버전과 전체 내용 기준이 필요합니다."
                    )
                base, basis, basis_hash = (
                    course["version"],
                    encode(current["snapshot"]),
                    current["hash"],
                )
                author_checks = encode(
                    {
                        key: {"state": "pending", "note": value["note"]}
                        for key, value in normalize_checks(
                            data.get("checks", json.loads(author_checks))
                        ).items()
                    }
                )
                author_note = (
                    text_field(data, "note", maximum=4000, optional=True)
                    if "note" in data
                    else author_note
                )
                reviewer_checks = "{}"
            elif action == "submit":
                if state != "draft" or stale:
                    raise ApiError(
                        409,
                        "최신 내용의 검수 초안만 제출할 수 있습니다. 현재 기준을 비교해주세요.",
                    )
                normalize_checks(json.loads(author_checks), complete=True)
                if not current["ready"]:
                    raise ApiError(409, " / ".join(current["blockers"])[:1800])
                if course["status"] == "draft":
                    c.execute(
                        "UPDATE courses SET status='review',version=version+1,updated_at=? WHERE id=?",
                        (now_iso(), cid),
                    )
                    course = c.execute(
                        "SELECT * FROM courses WHERE id=?", (cid,)
                    ).fetchone()
                    current = self.current(c, course)
                base, basis, basis_hash = (
                    course["version"],
                    encode(current["snapshot"]),
                    current["hash"],
                )
                state, submitted_at = "submitted", now_iso()
                for row in c.execute(
                    "SELECT id FROM users WHERE role='admin' AND status='active' AND id!=?",
                    (user["id"],),
                ):
                    notify(
                        c,
                        row["id"],
                        "강의 품질 검수 요청",
                        course["title"],
                        "/studio/course/" + cid + "/publication?review=" + rid,
                        event_key="publication.submit:"
                        + rid
                        + ":"
                        + str(data["version"]),
                        category="operation",
                        reference_kind="course_staff",
                        reference_id=cid,
                        group_key="course:" + cid,
                    )
            elif action in ("publish", "reject"):
                if state != "submitted":
                    raise ApiError(409, "검수 요청을 제출한 뒤 결정해주세요.")
                if action == "publish":
                    if stale or not current["ready"]:
                        raise ApiError(
                            409,
                            "검수 대상 또는 제공 조건이 바뀌었습니다. 요청 회수 후 새 검수가 필요합니다. "
                            + " / ".join(current["blockers"])[:1200],
                        )
                    normalize_checks(json.loads(reviewer_checks), True, True)
                    self.app.platform.content.validate_publication(c, course)
                    c.execute(
                        "UPDATE courses SET status='published',version=version+1,updated_at=? WHERE id=?",
                        (now_iso(), cid),
                    )
                    published_version, state = course["version"] + 1, "published"
                    audit(
                        c,
                        user,
                        "course.published",
                        "course",
                        cid,
                        {
                            "reviewId": rid,
                            "reviewedHash": basis_hash,
                            "publishedVersion": published_version,
                        },
                    )
                else:
                    if not any(
                        value["state"] == "changes"
                        for value in normalize_checks(
                            json.loads(reviewer_checks), True
                        ).values()
                    ):
                        raise ApiError(
                            409,
                            "수정이 필요한 항목과 구체적인 의견을 먼저 저장해주세요.",
                        )
                    state = "changes_requested"
                reviewer_id, decision_reason, decided_at = user["id"], reason, now_iso()
            elif action == "withdraw":
                state, decided_at, decision_reason = "withdrawn", now_iso(), reason
            if (
                state in ("changes_requested", "withdrawn")
                and course["status"] == "review"
            ):
                c.execute(
                    "UPDATE courses SET status='draft',version=version+1,updated_at=? WHERE id=?",
                    (now_iso(), cid),
                )
            c.execute(
                "UPDATE course_reviews SET status=?,base_version=?,snapshot=?,snapshot_hash=?,author_checks=?,reviewer_checks=?,author_note=?,review_note=?,decision_reason=?,reviewer_id=?,submitted_at=?,decided_at=?,published_version=?,version=version+1,updated_at=? WHERE id=?",
                (
                    state,
                    base,
                    basis,
                    basis_hash,
                    author_checks,
                    reviewer_checks,
                    author_note,
                    review_note,
                    decision_reason,
                    reviewer_id,
                    submitted_at,
                    decided_at,
                    published_version,
                    now_iso(),
                    rid,
                ),
            )
            self.event(
                c,
                user,
                rid,
                action,
                reason,
                {
                    "courseVersion": current["courseVersion"],
                    "reviewedHash": basis_hash,
                    "authorChecks": json.loads(author_checks),
                    "reviewerChecks": json.loads(reviewer_checks),
                    "authorNote": author_note,
                    "reviewNote": review_note,
                },
            )
            if (
                action in ("publish", "reject", "withdraw")
                and old["author_id"] != user["id"]
            ):
                notify(
                    c,
                    old["author_id"],
                    {
                        "publish": "강의가 발행됐습니다",
                        "reject": "강의 보완 요청",
                        "withdraw": "검수 요청 회수",
                    }[action],
                    reason,
                    "/studio/course/" + cid + "/publication?review=" + rid,
                    event_key="publication.decision:"
                    + rid
                    + ":"
                    + str(data["version"]),
                    category="operation",
                    reference_kind="course_staff",
                    reference_id=cid,
                    group_key="course:" + cid,
                )
            course = c.execute("SELECT * FROM courses WHERE id=?", (cid,)).fetchone()
            return {
                "review": self.view(c, self.row(c, cid, rid), self.current(c, course))
            }

    def comment(self, user, cid, rid, data):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, _ = managed(c, user, cid)
            row = self.row(c, cid, rid)
            check_id = data.get("checkId") or None
            if check_id is not None and check_id not in CHECK_IDS:
                raise ApiError(400, "의견을 연결할 품질 항목을 확인해주세요.")
            body = text_field(data, "body", minimum=5, maximum=4000)
            if (
                c.execute(
                    "SELECT COUNT(*) FROM course_review_comments WHERE review_id=?",
                    (rid,),
                ).fetchone()[0]
                >= 500
            ):
                raise ApiError(
                    409,
                    "검수 의견 한도에 도달했습니다. 새 검수에서 보완 내용을 이어가세요.",
                )
            comment_id = new_id("reviewcomment")
            c.execute(
                "INSERT INTO course_review_comments VALUES(?,?,?,?,?,?)",
                (comment_id, rid, user["id"], check_id, body, now_iso()),
            )
            audit(
                c,
                user,
                "publication.comment",
                "course_review",
                rid,
                {"commentId": comment_id, "checkId": check_id},
            )
            if row["author_id"] != user["id"]:
                notify(
                    c,
                    row["author_id"],
                    "강의 검수 의견",
                    body,
                    "/studio/course/" + cid + "/publication?review=" + rid,
                    event_key="publication.comment:" + comment_id,
                    category="operation",
                    reference_kind="course_staff",
                    reference_id=cid,
                    group_key="course:" + cid,
                )
            return {"commentId": comment_id}
