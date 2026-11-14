"""Published lessons change only after an explicit, version-checked review."""

import json
from ..core import ApiError, text_field, choice_field, now_iso, new_id
from .common import audit, notify, page_args, require_role, row_dict, version
from .history import editable_snapshot, lesson_snapshot


class RevisionService:
    def __init__(self, app, content):
        self.app = app
        self.content = content

    def lesson(self, c, course_id, lesson_id):
        row = c.execute(
            "SELECT * FROM course_lessons WHERE course_id=? AND id=?",
            (course_id, lesson_id),
        ).fetchone()
        if not row:
            raise ApiError(404, "차시를 찾을 수 없습니다.")
        return row

    def revision(self, c, user, revision_id):
        require_role(c, user, "coach")
        row = c.execute(
            "SELECT r.*,u.name AS author,v.name AS reviewer FROM lesson_revisions r LEFT JOIN users u ON u.id=r.author_id LEFT JOIN users v ON v.id=r.reviewer_id WHERE r.id=?",
            (revision_id,),
        ).fetchone()
        if not row:
            raise ApiError(404, "변경안을 찾을 수 없습니다.")
        course = self.content.managed_course(c, user, row["course_id"])
        return row, course

    def detail_data(self, c, user, row, course):
        live = self.lesson(c, row["course_id"], row["lesson_id"])
        owner = row["author_id"] == user["id"] or user["role"] == "admin"
        publication = c.execute(
            "SELECT lesson_version FROM lesson_history WHERE revision_id=?",
            (row["id"],),
        ).fetchone()
        return {
            "id": row["id"],
            "courseId": row["course_id"],
            "lessonId": row["lesson_id"],
            "courseTitle": course["title"],
            "authorId": row["author_id"],
            "author": row["author"],
            "reviewer": row["reviewer"],
            "status": row["status"],
            "baseVersion": row["base_version"],
            "version": row["version"],
            "reason": row["reason"],
            "reviewNote": row["review_note"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "submittedAt": row["submitted_at"],
            "reviewedAt": row["reviewed_at"],
            "baseLesson": json.loads(row["base_snapshot"]),
            "proposedLesson": json.loads(row["payload"]),
            "currentLesson": lesson_snapshot(live),
            "stale": live["version"] != row["base_version"],
            "appliedLessonVersion": publication[0] if publication else None,
            "canEdit": owner and row["status"] == "draft",
            "canWithdraw": owner and row["status"] == "submitted",
            "canCancel": owner and row["status"] in ("draft", "submitted"),
            "canReview": user["role"] == "admin" and row["status"] == "submitted",
            "events": [
                {**row_dict(event), "actor": event["actor"] or "이전 계정"}
                for event in c.execute(
                    "SELECT e.*,u.name AS actor FROM revision_events e LEFT JOIN users u ON u.id=e.actor_id WHERE e.revision_id=? ORDER BY e.version,e.created_at,e.id",
                    (row["id"],),
                )
            ],
        }

    def detail(self, user, revision_id):
        with self.app.db.connect() as c:
            row, course = self.revision(c, user, revision_id)
            return {"revision": self.detail_data(c, user, row, course)}

    def event(self, c, user, revision_id, action, note=""):
        row = c.execute(
            "SELECT status,version FROM lesson_revisions WHERE id=?", (revision_id,)
        ).fetchone()
        c.execute(
            "INSERT INTO revision_events VALUES(?,?,?,?,?,?,?,?)",
            (
                new_id("revisionevent"),
                revision_id,
                user["id"],
                action,
                row["status"],
                row["version"],
                note,
                now_iso(),
            ),
        )
        audit(
            c,
            user,
            "revision." + action,
            "lesson-revision",
            revision_id,
            {"version": row["version"], "status": row["status"], "note": note},
        )

    def create(self, user, course_id, lesson_id):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            require_role(c, user, "coach")
            course = self.content.managed_course(c, user, course_id)
            lesson = self.lesson(c, course_id, lesson_id)
            if (
                course["status"] not in ("published", "archived")
                or lesson["status"] != "published"
            ):
                raise ApiError(
                    409, "발행된 강의의 공개 차시에서 변경안을 만들 수 있습니다."
                )
            active = c.execute(
                "SELECT id FROM lesson_revisions WHERE course_id=? AND lesson_id=? AND status IN ('draft','submitted')",
                (course_id, lesson_id),
            ).fetchone()
            if active:
                revision_id = active["id"]
            else:
                revision_id = new_id("revision")
                c.execute(
                    "INSERT INTO lesson_revisions(id,course_id,lesson_id,author_id,base_version,base_snapshot,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        revision_id,
                        course_id,
                        lesson_id,
                        user["id"],
                        lesson["version"],
                        json.dumps(lesson_snapshot(lesson), ensure_ascii=False),
                        json.dumps(editable_snapshot(lesson), ensure_ascii=False),
                        now_iso(),
                        now_iso(),
                    ),
                )
                self.event(
                    c,
                    user,
                    revision_id,
                    "created",
                    "공개 차시에서 변경안 초안을 만들었습니다.",
                )
        return self.detail(user, revision_id)

    def list(self, user, query):
        page, limit, offset = page_args(query)
        with self.app.db.connect() as c:
            require_role(c, user, "coach")
            where = []
            args = []
            if user["role"] != "admin":
                where.append("co.instructor_user_id=?")
                args.append(user["id"])
            course_id = query.get("courseId", [""])[0]
            if course_id:
                self.content.managed_course(c, user, course_id)
                where.append("r.course_id=?")
                args.append(course_id)
            status = query.get("status", ["active"])[0]
            if status == "active":
                where.append("r.status IN ('draft','submitted')")
            elif status in ("draft", "submitted", "approved", "rejected", "cancelled"):
                where.append("r.status=?")
                args.append(status)
            elif status != "all":
                raise ApiError(400, "변경안 상태를 확인해주세요.")
            condition = " WHERE " + " AND ".join(where) if where else ""
            joins = " FROM lesson_revisions r JOIN courses co ON co.id=r.course_id JOIN course_lessons l ON l.course_id=r.course_id AND l.id=r.lesson_id LEFT JOIN users u ON u.id=r.author_id"
            total = c.execute("SELECT COUNT(*)" + joins + condition, args).fetchone()[0]
            rows = c.execute(
                "SELECT r.id,r.course_id,r.lesson_id,r.author_id,r.status,r.version,r.base_version,r.reason,r.created_at,r.updated_at,u.name AS author,co.title AS courseTitle,l.title AS lessonTitle,l.version AS currentVersion"
                + joins
                + condition
                + " ORDER BY r.updated_at DESC,r.id DESC LIMIT ? OFFSET ?",
                args + [limit, offset],
            ).fetchall()
            return {
                "revisions": [
                    {
                        **row_dict(row),
                        "baseVersion": row["base_version"],
                        "stale": row["currentVersion"] != row["base_version"],
                    }
                    for row in rows
                ],
                "total": total,
                "page": page,
                "pageSize": limit,
            }

    def update(self, user, revision_id, data):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row, course = self.revision(c, user, revision_id)
            if row["author_id"] != user["id"] and user["role"] != "admin":
                raise ApiError(403, "작성자 또는 관리자만 변경안을 수정할 수 있습니다.")
            version(data, row)
            if row["status"] in ("approved", "rejected", "cancelled"):
                raise ApiError(
                    409, "결정된 변경안은 수정할 수 없습니다. 새 변경안을 작성해주세요."
                )
            action = choice_field(
                data, "action", ("save", "submit", "withdraw", "cancel", "rebase")
            )
            live = self.lesson(c, row["course_id"], row["lesson_id"])
            if action == "cancel":
                c.execute(
                    "UPDATE lesson_revisions SET status='cancelled',version=version+1,updated_at=? WHERE id=?",
                    (now_iso(), revision_id),
                )
                self.event(
                    c,
                    user,
                    revision_id,
                    "cancelled",
                    "작성자 또는 관리자가 변경안을 중단했습니다.",
                )
            elif action == "withdraw":
                if row["status"] != "submitted":
                    raise ApiError(409, "검수 요청한 변경안만 회수할 수 있습니다.")
                c.execute(
                    "UPDATE lesson_revisions SET status='draft',submitted_at=NULL,version=version+1,updated_at=? WHERE id=?",
                    (now_iso(), revision_id),
                )
                self.event(
                    c,
                    user,
                    revision_id,
                    "withdrawn",
                    "검수 요청을 회수하고 초안으로 되돌렸습니다.",
                )
            else:
                if row["status"] != "draft":
                    raise ApiError(
                        409, "검수 중인 변경안은 먼저 요청을 회수한 뒤 수정해주세요."
                    )
                if course["status"] not in ("published", "archived"):
                    raise ApiError(409, "강의가 발행 또는 보관 상태여야 합니다.")
                if action == "rebase":
                    c.execute(
                        "UPDATE lesson_revisions SET base_version=?,base_snapshot=?,version=version+1,updated_at=? WHERE id=?",
                        (
                            live["version"],
                            json.dumps(lesson_snapshot(live), ensure_ascii=False),
                            now_iso(),
                            revision_id,
                        ),
                    )
                    self.event(
                        c,
                        user,
                        revision_id,
                        "rebased",
                        "최신 공개 차시를 기준으로 다시 비교합니다. 작성 내용은 유지했습니다.",
                    )
                else:
                    proposed = data.get("lesson")
                    if not isinstance(proposed, dict):
                        raise ApiError(400, "변경할 차시 내용을 확인해주세요.")
                    proposed = self.content.prepare_lesson(c, course, proposed, live)
                    reason = text_field(
                        data,
                        "reason",
                        minimum=5 if action == "submit" else 0,
                        maximum=2000,
                        optional=action == "save",
                    )
                    if action == "submit":
                        if live["version"] != row["base_version"]:
                            raise ApiError(
                                409,
                                "공개 차시가 변경되었습니다. 최신 차시와 다시 비교한 뒤 검수를 요청해주세요.",
                            )
                        if proposed == editable_snapshot(live):
                            raise ApiError(400, "공개 차시와 달라진 내용이 없습니다.")
                    status = "submitted" if action == "submit" else "draft"
                    c.execute(
                        "UPDATE lesson_revisions SET payload=?,reason=?,status=?,submitted_at=?,version=version+1,updated_at=? WHERE id=?",
                        (
                            json.dumps(proposed, ensure_ascii=False),
                            reason,
                            status,
                            now_iso() if action == "submit" else None,
                            now_iso(),
                            revision_id,
                        ),
                    )
                    self.event(
                        c,
                        user,
                        revision_id,
                        "submitted" if action == "submit" else "saved",
                        reason,
                    )
                    if action == "submit":
                        for target in c.execute(
                            "SELECT id FROM users WHERE role='admin' AND status='active' AND id<>?",
                            (user["id"],),
                        ):
                            notify(
                                c,
                                target["id"],
                                "차시 변경안 검수를 요청했습니다",
                                course["title"] + " · " + live["title"],
                                "#/studio/revisions/" + revision_id,
                                event_key="revision.submit:"
                                + revision_id
                                + ":"
                                + str(data["version"]),
                                category="operation",
                                reference_kind="revision",
                                reference_id=revision_id,
                                group_key="revision:" + revision_id,
                            )
        return self.detail(user, revision_id)

    def review(self, user, revision_id, data):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            require_role(c, user, "admin")
            row, course = self.revision(c, user, revision_id)
            action = choice_field(data, "action", ("approve", "reject"))
            final = "approved" if action == "approve" else "rejected"
            if row["status"] == final:
                return {"revision": self.detail_data(c, user, row, course)}
            version(data, row)
            if row["status"] != "submitted":
                raise ApiError(
                    409, "검수 요청 상태에서만 승인 또는 반려할 수 있습니다."
                )
            note = text_field(data, "note", minimum=5, maximum=2000)
            if action == "approve":
                if course["status"] not in ("published", "archived"):
                    raise ApiError(
                        409, "현재 강의는 변경안을 반영할 수 없는 상태입니다."
                    )
                live = self.lesson(c, row["course_id"], row["lesson_id"])
                if live["version"] != row["base_version"]:
                    raise ApiError(
                        409,
                        "공개 차시가 변경되어 반영을 중단했습니다. 작성자가 최신 차시와 다시 비교한 뒤 검수를 요청해야 합니다.",
                    )
                proposed = json.loads(row["payload"])
                proposed["version"] = live["version"]
                self.content.write_lesson(
                    c,
                    user,
                    row["course_id"],
                    proposed,
                    row["lesson_id"],
                    source="revision",
                    reason=row["reason"] + " / 검수: " + note,
                    revision_id=revision_id,
                )
            c.execute(
                "UPDATE lesson_revisions SET status=?,review_note=?,reviewer_id=?,reviewed_at=?,version=version+1,updated_at=? WHERE id=?",
                (final, note, user["id"], now_iso(), now_iso(), revision_id),
            )
            self.event(c, user, revision_id, final, note)
            notify(
                c,
                row["author_id"],
                "차시 변경안이 "
                + ("반영되었습니다" if action == "approve" else "반려되었습니다"),
                note,
                "#/studio/revisions/" + revision_id,
                event_key="revision.review:" + revision_id + ":" + str(data["version"]),
                category="operation",
                reference_kind="revision",
                reference_id=revision_id,
                group_key="revision:" + revision_id,
            )
        return self.detail(user, revision_id)

    def history(self, user, course_id, lesson_id, version_number=None):
        with self.app.db.connect() as c:
            require_role(c, user, "coach")
            self.content.managed_course(c, user, course_id)
            live = self.lesson(c, course_id, lesson_id)
            if version_number is not None:
                row = c.execute(
                    "SELECT h.*,u.name AS actor FROM lesson_history h LEFT JOIN users u ON u.id=h.actor_id WHERE h.course_id=? AND h.lesson_id=? AND h.lesson_version=?",
                    (course_id, lesson_id, version_number),
                ).fetchone()
                if not row:
                    raise ApiError(404, "발행 버전을 찾을 수 없습니다.")
                return {
                    "entry": {**row_dict(row), "snapshot": json.loads(row["snapshot"])}
                }
            rows = c.execute(
                "SELECT h.id,h.lesson_version,h.actor_id,h.source,h.revision_id,h.reason,h.created_at,h.snapshot,u.name AS actor FROM lesson_history h LEFT JOIN users u ON u.id=h.actor_id WHERE h.course_id=? AND h.lesson_id=? ORDER BY h.lesson_version DESC LIMIT 30",
                (course_id, lesson_id),
            ).fetchall()
            return {
                "currentLesson": lesson_snapshot(live),
                "entries": [
                    {
                        **{
                            key: value
                            for key, value in row_dict(row).items()
                            if key != "snapshot"
                        },
                        "lessonVersion": row["lesson_version"],
                        "revisionId": row["revision_id"],
                        "lessonStatus": json.loads(row["snapshot"])["status"],
                    }
                    for row in rows
                ],
            }
