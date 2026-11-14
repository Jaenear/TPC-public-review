"""Course Q/A and moderated community. No user HTML is trusted."""

import json

from ..core import ApiError, text_field, choice_field, now_iso, new_id
from ..discussion_records import initialize, metadata
from ..community_records import blocked_pair, visibility_sql
from ..moderation_records import observe, observe_report
from .moderation_shared import actor as moderation_actor
from .learning_start import active_member
from .discussion_history import DiscussionHistory
from .common import (
    audit,
    notify,
    row_dict,
    roles,
    require_role,
    require_course_access,
    can_manage_course,
    page_args,
    version,
)


class DiscussionService:
    def __init__(self, app):
        self.app = app
        self.history = DiscussionHistory(self)

    def course(self, c, course_id):
        row = c.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        if not row:
            raise ApiError(404, "강의를 찾을 수 없습니다.")
        return row

    def question(self, c, user, question_id):
        user = active_member(c, user)
        row = c.execute(
            "SELECT q.*,u.name AS author FROM questions q JOIN users u ON u.id=q.user_id WHERE q.id=?",
            (question_id,),
        ).fetchone()
        if not row:
            raise ApiError(404, "질문을 찾을 수 없습니다.")
        course = self.course(c, row["course_id"])
        require_course_access(c, user, course)
        head = c.execute(
            "SELECT hidden FROM question_content_heads WHERE target_id=?",
            (question_id,),
        ).fetchone()
        if head and head["hidden"] and not can_manage_course(c, user, course):
            raise ApiError(404, "질문을 찾을 수 없습니다.")
        if (
            row["visibility"] == "private"
            and row["user_id"] != user["id"]
            and not can_manage_course(c, user, course)
        ):
            raise ApiError(404, "질문을 찾을 수 없습니다.")
        return row, course

    def question_detail(self, user, question_id):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user = active_member(c, user)
            question, course = self.question(c, user, question_id)
            item = row_dict(question)
            item.update(metadata(c, "question", question_id))
            item["courseTitle"] = course["title"]
            item["canAnswerOfficially"] = can_manage_course(c, user, course)
            item.update(self.content_view(c, user, "question", question_id))
            item["answers"] = [
                {
                    **row_dict(row),
                    "official": bool(row["official"]),
                    **metadata(c, "answer", row["id"]),
                    **self.content_view(c, user, "answer", row["id"]),
                }
                for row in c.execute(
                    "SELECT a.*,u.name AS author FROM answers a JOIN users u ON u.id=a.user_id JOIN answer_content_heads h ON h.target_id=a.id WHERE a.question_id=? AND (h.hidden=0 OR ?=1) ORDER BY a.created_at,a.id",
                    (question_id, int(can_manage_course(c, user, course))),
                )
            ]
            return {"question": item}

    def content_view(self, c, user, kind, target_id):
        allowed = {}
        for name, options in (
            ("canEdit", {"editing": True}),
            ("canViewHistory", {"history_read": True}),
        ):
            try:
                self.history.scope(c, user, kind, target_id, **options)
                allowed[name] = True
            except ApiError as error:
                if error.status not in (401, 403, 404, 409):
                    raise
                allowed[name] = False
        head = c.execute(
            f"SELECT revision_id,question_id FROM {kind}_content_heads WHERE target_id=?",
            (target_id,),
        ).fetchone()
        snapshot = c.execute(
            f"SELECT after_snapshot FROM {kind}_revisions WHERE id=?",
            (head["revision_id"],),
        ).fetchone()
        allowed["contentAttachments"] = [
            self.app.platform.discussion_attachments.public_reference(
                c, head["question_id"], aid
            )
            for aid in json.loads(snapshot[0])["attachments"]
        ]
        return allowed

    def questions(self, user, query, studio=False):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        page, limit, offset = page_args(query)
        where = []
        args = []
        course_id = query.get("courseId", [""])[0]
        with self.app.db.connect() as c:
            user = active_member(c, user)
            if user["role"] != "admin":
                where.append("(h.hidden=0 OR (co.instructor_user_id=? AND ?=1))")
                args.extend((user["id"], int("coach" in roles(c, user))))
            if studio:
                require_role(c, user, "coach")
                if user["role"] != "admin":
                    where.append("co.instructor_user_id=?")
                    args.append(user["id"])
                if course_id:
                    where.append("q.course_id=?")
                    args.append(course_id)
            elif course_id:
                course = self.course(c, course_id)
                require_course_access(c, user, course)
                where.append("q.course_id=?")
                args.append(course_id)
                if not can_manage_course(c, user, course):
                    where.append("(q.visibility='course' OR q.user_id=?)")
                    args.append(user["id"])
            else:
                where.append("q.user_id=?")
                args.append(user["id"])
            status = query.get("status", [""])[0]
            if status in ("open", "answered", "closed"):
                where.append("q.status=?")
                args.append(status)
            if query.get("lessonId", [""])[0]:
                where.append("q.lesson_id=?")
                args.append(query["lessonId"][0])
            condition = " WHERE " + " AND ".join(where) if where else ""
            joins = " FROM questions q JOIN question_content_heads h ON h.target_id=q.id JOIN courses co ON co.id=q.course_id JOIN users u ON u.id=q.user_id"
            total = c.execute("SELECT COUNT(*)" + joins + condition, args).fetchone()[0]
            rows = c.execute(
                "SELECT q.*,u.name AS author,co.title AS courseTitle,(SELECT COUNT(*) FROM answers a WHERE a.question_id=q.id) AS answerCount"
                + joins
                + condition
                + " ORDER BY q.updated_at DESC,q.id DESC LIMIT ? OFFSET ?",
                args + [limit, offset],
            ).fetchall()
            return {
                "questions": [row_dict(row) for row in rows],
                "page": page,
                "total": total,
                "pageSize": limit,
            }

    def create_question(self, user, data):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        self.app.limiter.check(("question", user["id"]), 30, 3600)
        course_id = text_field(data, "courseId", maximum=100)
        lesson = data.get("lessonId") or None
        title = text_field(data, "title", minimum=3, maximum=150)
        body = text_field(data, "body", minimum=10, maximum=10000)
        visibility = choice_field(data, "visibility", ("course", "private"))
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = active_member(c, user)
            course = self.course(c, course_id)
            require_course_access(c, user, course)
            if (
                lesson
                and not c.execute(
                    "SELECT 1 FROM course_lessons WHERE course_id=? AND id=? AND status='published'",
                    (course_id, lesson),
                ).fetchone()
            ):
                raise ApiError(400, "이 강의의 공개 차시를 선택해주세요.")
            question_id = new_id("question")
            timestamp = now_iso()
            c.execute(
                "INSERT INTO questions(id,course_id,lesson_id,user_id,title,body,visibility,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    question_id,
                    course_id,
                    lesson,
                    user["id"],
                    title,
                    body,
                    visibility,
                    timestamp,
                    timestamp,
                ),
            )
            initialize(
                c,
                "question",
                c.execute(
                    "SELECT * FROM questions WHERE id=?", (question_id,)
                ).fetchone(),
                "created",
                user["id"],
            )
            instructor = course["instructor_user_id"]
            targets = (
                [instructor]
                if instructor
                and c.execute(
                    "SELECT 1 FROM users WHERE id=? AND status='active'", (instructor,)
                ).fetchone()
                else [
                    row[0]
                    for row in c.execute(
                        "SELECT id FROM users WHERE role='admin' AND status='active'"
                    )
                ]
            )
            for target in targets:
                if target != user["id"]:
                    notify(
                        c,
                        target,
                        "새 강의 질문이 등록되었습니다",
                        title,
                        "#/qa/" + question_id,
                        event_key="qa.question:" + question_id,
                        category="learning",
                        reference_kind="question",
                        reference_id=question_id,
                        group_key="question:" + question_id,
                    )
        return self.question_detail(user, question_id)

    def answer(self, user, question_id, data):
        body = text_field(data, "body", minimum=2, maximum=10000)
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        self.app.limiter.check(("answer", user["id"]), 60, 3600)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = active_member(c, user)
            question, course = self.question(c, user, question_id)
            if question["status"] == "closed":
                raise ApiError(409, "닫힌 질문에는 답변을 추가할 수 없습니다.")
            official = can_manage_course(c, user, course)
            answer_id = new_id("answer")
            c.execute(
                "INSERT INTO answers(id,question_id,user_id,body,official,created_at) VALUES(?,?,?,?,?,?)",
                (
                    answer_id,
                    question_id,
                    user["id"],
                    body,
                    int(official),
                    now_iso(),
                ),
            )
            initialize(
                c,
                "answer",
                c.execute("SELECT * FROM answers WHERE id=?", (answer_id,)).fetchone(),
                "created",
                user["id"],
            )
            status = (
                "answered"
                if official
                else "open" if question["user_id"] == user["id"] else question["status"]
            )
            accepted = (
                None
                if official or question["user_id"] == user["id"]
                else question["accepted_answer_id"]
            )
            c.execute(
                "UPDATE questions SET status=?,accepted_answer_id=?,version=version+1,updated_at=? WHERE id=?",
                (status, accepted, now_iso(), question_id),
            )
            target = (
                course["instructor_user_id"]
                if user["id"] == question["user_id"]
                else question["user_id"]
            )
            if target and target != user["id"]:
                notify(
                    c,
                    target,
                    "강의 질문에 새 답변이 있습니다",
                    question["title"],
                    "#/qa/" + question_id,
                    event_key="qa.answer:" + answer_id,
                    category="learning",
                    reference_kind="question",
                    reference_id=question_id,
                    group_key="question:" + question_id,
                )
            if official:
                audit(c, user, "qa.official_answer", "question", question_id)
        return self.question_detail(user, question_id)

    def update_question(self, user, question_id, data):
        action = choice_field(data, "action", ("resolve", "close", "reopen"))
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = active_member(c, user)
            question, course = self.question(c, user, question_id)
            version(data, question)
            owner = user["id"] == question["user_id"]
            manager = can_manage_course(c, user, course)
            if not owner and not manager:
                raise ApiError(403, "질문 작성자 또는 담당 코치만 처리할 수 있습니다.")
            answer_id = None
            if action == "resolve":
                if not owner and user["role"] != "admin":
                    raise ApiError(403, "질문 작성자가 해결 답변을 선택할 수 있습니다.")
                answer_id = text_field(data, "answerId", maximum=100)
                answer = c.execute(
                    "SELECT * FROM answers WHERE id=? AND question_id=?",
                    (answer_id, question_id),
                ).fetchone()
                if not answer or answer["user_id"] == question["user_id"]:
                    raise ApiError(
                        400, "이 질문에 등록된 다른 사람의 답변을 선택해주세요."
                    )
            status = {"resolve": "answered", "close": "closed", "reopen": "open"}[
                action
            ]
            c.execute(
                "UPDATE questions SET status=?,accepted_answer_id=?,version=version+1,updated_at=? WHERE id=?",
                (status, answer_id, now_iso(), question_id),
            )
            audit(c, user, "qa." + action, "question", question_id)
        return self.question_detail(user, question_id)

    def board(self, c, user, board_id, writing=False):
        board = c.execute("SELECT * FROM boards WHERE id=?", (board_id,)).fetchone()
        if not board:
            raise ApiError(404, "게시판을 찾을 수 없습니다.")
        if (board["read_access"] != "public" or writing) and not user:
            raise ApiError(401, "회원 전용 게시판입니다. 로그인해주세요.")
        if writing and board["write_access"] == "staff":
            require_role(c, user, "moderator")
        return board

    def is_moderator(self, c, user):
        return bool(roles(c, user).intersection({"admin", "moderator"}))

    def post(self, c, user, post_id):
        post = c.execute(
            "SELECT p.*,u.name AS author,b.title AS boardTitle FROM posts p JOIN users u ON u.id=p.user_id JOIN boards b ON b.id=p.board_id WHERE p.id=?",
            (post_id,),
        ).fetchone()
        if not post:
            raise ApiError(404, "게시글을 찾을 수 없습니다.")
        self.board(c, user, post["board_id"])
        if post["status"] != "published" and not (
            user and (post["user_id"] == user["id"] or self.is_moderator(c, user))
        ):
            raise ApiError(404, "게시글을 찾을 수 없습니다.")
        return post

    def list_posts(self, user, query):
        page, limit, offset = page_args(query)
        board_id = query.get("board", ["general"])[0]
        search = query.get("q", [""])[0].strip()[:100]
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user = active_member(c, user)
            board = self.board(c, user, board_id)
            where = " WHERE p.board_id=? AND p.status='published'"
            args = [board_id]
            if search:
                where += " AND (p.title LIKE ? OR p.body LIKE ?)"
                args.extend(["%" + search + "%"] * 2)
            joins = " FROM posts p JOIN users u ON u.id=p.user_id"
            total = c.execute("SELECT COUNT(*)" + joins + where, args).fetchone()[0]
            visibility, visibility_args = visibility_sql(user)
            rows = c.execute(
                "SELECT p.*,u.name AS author,(SELECT COUNT(*) FROM comments cm LEFT JOIN moderation_comment_heads mh ON mh.comment_id=cm.id WHERE cm.post_id=p.id AND cm.status='published' AND COALESCE(mh.owner_deleted,0)=0"
                + visibility
                + ") AS commentCount,(SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id=p.id) AS likeCount"
                + joins
                + where
                + " ORDER BY p.pinned DESC,p.created_at DESC,p.id DESC LIMIT ? OFFSET ?",
                visibility_args + args + [limit, offset],
            ).fetchall()
            return {
                "board": row_dict(board),
                "boards": [row_dict(row) for row in c.execute("SELECT * FROM boards")],
                "posts": [row_dict(row) for row in rows],
                "page": page,
                "pageSize": limit,
                "total": total,
            }

    def post_detail(self, user, post_id):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user = active_member(c, user)
            post = self.post(c, user, post_id)
            return self.post_detail_in(c, user, post)

    def post_detail_in(self, c, user, post):
        """Build the response in the caller's current permission snapshot."""
        post_id = post["id"]
        item = row_dict(post)
        item["canComment"] = bool(
            user
            and post["status"] == "published"
            and not post["locked"]
            and not blocked_pair(c, user["id"], post["user_id"])
        )
        item["canModerate"] = self.is_moderator(c, user)
        if item["canModerate"]:
            policy = self.app.platform.moderation.policies.current(c)
            item["moderation"] = {
                "target": observe(c, "post", post_id),
                "policyId": policy["id"] if policy else None,
            }
        item["likeCount"] = c.execute(
            "SELECT COUNT(*) FROM post_likes WHERE post_id=?", (post_id,)
        ).fetchone()[0]
        item["liked"] = bool(
            user
            and c.execute(
                "SELECT 1 FROM post_likes WHERE post_id=? AND user_id=?",
                (post_id, user["id"]),
            ).fetchone()
        )
        item["comments"] = self.app.platform.community_comments.listing(c, user, post)
        return {"post": item}

    def save_post(self, user, data, post_id=None):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        title = text_field(data, "title", minimum=3, maximum=150)
        body = text_field(data, "body", minimum=10, maximum=20000)
        self.app.limiter.check(("post", user["id"]), 30, 3600)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = moderation_actor(c, user)
            if post_id:
                old = self.post(c, user, post_id)
                version(data, old)
                if old["user_id"] != user["id"]:
                    raise ApiError(403, "작성한 게시글만 수정할 수 있습니다.")
                if old["locked"] or old["status"] != "published":
                    raise ApiError(409, "운영 처리된 게시글은 수정할 수 없습니다.")
                c.execute(
                    "UPDATE posts SET title=?,body=?,version=version+1,updated_at=? WHERE id=?",
                    (title, body, now_iso(), post_id),
                )
                audit(c, user, "post.edited", "post", post_id)
            else:
                board_id = text_field(data, "boardId", maximum=100)
                self.board(c, user, board_id, writing=True)
                post_id = new_id("post")
                c.execute(
                    "INSERT INTO posts(id,board_id,user_id,title,body,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (post_id, board_id, user["id"], title, body, now_iso(), now_iso()),
                )
        return self.post_detail(user, post_id)

    def comment(self, user, post_id, data):
        return self.app.platform.community_comments.create(user, post_id, data)

    def delete_comment(self, user, comment_id, data):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = moderation_actor(c, user)
            comment = c.execute(
                "SELECT * FROM comments WHERE id=?", (comment_id,)
            ).fetchone()
            if not comment:
                raise ApiError(404, "댓글을 찾을 수 없습니다.")
            owner = comment["user_id"] == user["id"]
            if owner:
                # A parent restriction must not erase the author's deletion
                # intent. This mutation returns no parent or comment content.
                parent = c.execute(
                    "SELECT board_id FROM posts WHERE id=?", (comment["post_id"],)
                ).fetchone()
                self.board(c, user, parent["board_id"])
            else:
                self.post(c, user, comment["post_id"])
                require_role(c, user, "moderator")
            reason = (
                "작성자 삭제"
                if owner
                else text_field(data, "reason", minimum=5, maximum=2000)
            )
            before = observe(c, "comment", comment_id)
            policy = (
                self.app.platform.moderation.current_policy(c, data)
                if not owner
                else None
            )
            if owner and not before["ownerDeleted"]:
                c.execute(
                    "UPDATE moderation_comment_heads SET owner_deleted=1,version=version+1 WHERE comment_id=?",
                    (comment_id,),
                )
                if comment["status"] != "published":
                    audit(
                        c,
                        user,
                        "comment.owner_deleted",
                        "comment",
                        comment_id,
                        {"reason": reason},
                    )
            if comment["status"] == "published":
                c.execute(
                    "UPDATE comments SET status='deleted' WHERE id=?", (comment_id,)
                )
                audit(
                    c,
                    user,
                    "comment.deleted",
                    "comment",
                    comment_id,
                    {"reason": reason},
                )
                if not owner:
                    c.execute(
                        "UPDATE moderation_comment_heads SET version=version+1 WHERE comment_id=?",
                        (comment_id,),
                    )
                    recorded_action = self.app.platform.moderation.legacy(
                        c, user, before, "comment_delete", reason, policy=policy
                    )
                    notify(
                        c,
                        comment["user_id"],
                        "댓글 운영 처리 안내",
                        reason,
                        "#/community/moderation/actions",
                        event_key="moderation.action:" + recorded_action["id"],
                        category="operation",
                        reference_kind="moderation_action",
                        reference_id=recorded_action["id"],
                        group_key="post:" + comment["post_id"],
                    )
            return {"deleted": True}

    def post_action(self, user, post_id, data):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        action = choice_field(
            data,
            "action",
            ("like", "delete", "hide", "restore", "lock", "unlock", "pin", "unpin"),
        )
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = moderation_actor(c, user)
            post = self.post(c, user, post_id)
            if action == "like":
                if post["status"] != "published":
                    raise ApiError(409, "공개 게시글에서만 이용할 수 있습니다.")
                if type(data.get("active")) is not bool:
                    raise ApiError(400, "좋아요 상태를 확인해주세요.")
                if data["active"]:
                    c.execute(
                        "INSERT OR IGNORE INTO post_likes(post_id,user_id) VALUES(?,?)",
                        (post_id, user["id"]),
                    )
                else:
                    c.execute(
                        "DELETE FROM post_likes WHERE post_id=? AND user_id=?",
                        (post_id, user["id"]),
                    )
            else:
                version(data, post)
                if action == "delete":
                    if post["user_id"] != user["id"]:
                        raise ApiError(403, "본인의 게시글만 삭제할 수 있습니다.")
                    field, value = "status", "deleted"
                    reason = "작성자 삭제"
                else:
                    require_role(c, user, "moderator")
                    reason = text_field(data, "reason", minimum=5, maximum=2000)
                    if post["status"] == "deleted" or (
                        action == "restore" and post["status"] != "hidden"
                    ):
                        raise ApiError(
                            409, "작성자 삭제와 운영 숨김 상태를 구분해 확인해주세요."
                        )
                    before = observe(c, "post", post_id)
                    policy = self.app.platform.moderation.current_policy(c, data)
                    field, value = {
                        "hide": ("status", "hidden"),
                        "restore": ("status", "published"),
                        "lock": ("locked", 1),
                        "unlock": ("locked", 0),
                        "pin": ("pinned", 1),
                        "unpin": ("pinned", 0),
                    }[action]
                c.execute(
                    "UPDATE posts SET "
                    + field
                    + "=?,version=version+1,updated_at=? WHERE id=?",
                    (value, now_iso(), post_id),
                )
                audit(c, user, "post." + action, "post", post_id, {"reason": reason})
                if action != "delete":
                    recorded_action = self.app.platform.moderation.legacy(
                        c, user, before, action, reason, policy=policy
                    )
                if post["user_id"] != user["id"]:
                    notify(
                        c,
                        post["user_id"],
                        "게시글 운영 처리 안내",
                        reason,
                        "#/community/moderation/actions",
                        event_key="moderation.action:" + recorded_action["id"],
                        category="operation",
                        reference_kind="moderation_action",
                        reference_id=recorded_action["id"],
                        group_key="post:" + post_id,
                    )
        return self.post_detail(user, post_id)

    def report(self, user, post_id, data):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        self.app.limiter.check(("report", user["id"]), 20, 3600)
        reason = text_field(data, "reason", minimum=5, maximum=2000)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = moderation_actor(c, user)
            post = self.post(c, user, post_id)
            if "version" in data:
                version(data, post)
            timestamp = now_iso()
            c.execute(
                "INSERT OR IGNORE INTO reports(id,post_id,user_id,reason,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (new_id("report"), post_id, user["id"], reason, timestamp, timestamp),
            )
            report = c.execute(
                "SELECT * FROM reports WHERE post_id=? AND user_id=?",
                (post_id, user["id"]),
            ).fetchone()
            observe_report(c, report["id"])
            return {
                "report": row_dict(
                    c.execute(
                        "SELECT * FROM reports WHERE post_id=? AND user_id=?",
                        (post_id, user["id"]),
                    ).fetchone()
                )
            }

    def reports(self, user):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user = moderation_actor(c, user, "moderator")
            require_role(c, user, "moderator")
            results = []
            policy = self.app.platform.moderation.policies.current(c)
            for record in c.execute(
                "SELECT r.*,p.title,p.body,p.status AS postStatus,u.name AS reporter FROM reports r JOIN posts p ON p.id=r.post_id JOIN users u ON u.id=r.user_id ORDER BY r.created_at DESC LIMIT 200"
            ).fetchall():
                item = row_dict(record)
                observation = c.execute(
                    "SELECT * FROM moderation_report_observations WHERE report_id=?",
                    (record["id"],),
                ).fetchone()
                target = observe(c, "post", record["post_id"])
                item["moderation"] = {
                    "target": target,
                    "policyId": policy["id"] if policy else None,
                    "observation": (
                        {
                            "origin": observation["origin"],
                            "snapshot": json.loads(observation["snapshot"]),
                            "observedAt": observation["observed_at"],
                        }
                        if observation
                        else None
                    ),
                    "sourceChanged": bool(
                        observation
                        and observation["content_hash"] != target["contentHash"]
                    ),
                }
                results.append(item)
            return {"reports": results}

    def resolve_report(self, user, report_id, data):
        action = choice_field(data, "action", ("dismiss", "hide"))
        reason = text_field(data, "reason", minimum=5, maximum=2000)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = moderation_actor(c, user, "moderator")
            require_role(c, user, "moderator")
            report = c.execute(
                "SELECT * FROM reports WHERE id=?", (report_id,)
            ).fetchone()
            if not report:
                raise ApiError(404, "신고를 찾을 수 없습니다.")
            if report["status"] != "open":
                raise ApiError(409, "이미 처리된 신고입니다.")
            before = observe(c, "post", report["post_id"])
            self.app.platform.moderation.report_context(c, report, before, data)
            policy = self.app.platform.moderation.current_policy(c, data)
            if action == "hide" and before["status"] == "deleted":
                raise ApiError(
                    409, "작성자가 삭제한 원문은 숨김 조치로 바꾸지 않습니다."
                )
            if action == "hide":
                c.execute(
                    "UPDATE posts SET status='hidden',version=version+1,updated_at=? WHERE id=?",
                    (now_iso(), report["post_id"]),
                )
            c.execute(
                "UPDATE reports SET status='resolved',resolution=?,resolved_by=?,updated_at=? WHERE id=?",
                (reason, user["id"], now_iso(), report_id),
            )
            audit(c, user, "report." + action, "report", report_id, {"reason": reason})
            recorded_action = self.app.platform.moderation.legacy(
                c,
                user,
                before,
                "hide" if action == "hide" else "report_dismiss",
                reason,
                policy=policy,
                report_id=report_id,
                inform=action == "hide",
            )
            notify(
                c,
                report["user_id"],
                "신고 처리 결과가 등록되었습니다",
                reason,
                "#/community",
                event_key="report.result:" + report_id,
                category="operation",
                reference_kind="report",
                reference_id=report_id,
                group_key="report:" + report_id,
            )
            return {
                "report": row_dict(
                    c.execute(
                        "SELECT * FROM reports WHERE id=?", (report_id,)
                    ).fetchone()
                )
            }
