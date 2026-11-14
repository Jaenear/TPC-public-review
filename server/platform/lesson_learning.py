"""Versioned, server-graded lesson attempts and separate completion receipts.

No read or migration invents historical facts. Writers recheck current access
under a write lock. A replay returns its receipt together with current state;
it never re-applies an old action over a later completion change.
"""

import hashlib
import json
import re

from ..access import denial, for_member
from ..core import ApiError, new_id, now_iso, text_field
from .common import audit, can_manage_course, page_args
from .learning_start import active_member

TABLES = {"quiz": "lesson_quiz_attempts", "completion": "lesson_progress_events"}


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def quiz_version(lesson, required=False):
    if lesson["quiz"] is not None:
        try:
            quiz = json.loads(lesson["quiz"])
            options = quiz["options"]
            answer = quiz["answer"]
            if isinstance(answer, str):
                answer = options.index(answer)
            if (
                not isinstance(quiz["question"], str)
                or not quiz["question"].strip()
                or not isinstance(options, list)
                or not 2 <= len(options) <= 6
                or not all(isinstance(o, str) and o.strip() for o in options)
                or type(answer) is not int
                or not 0 <= answer < len(options)
                or not isinstance(quiz.get("explanation", ""), str)
            ):
                raise ValueError("Invalid current quiz")
            payload = canonical(quiz)
            return {
                "quiz": quiz,
                "json": payload,
                "hash": hashlib.sha256(payload.encode()).hexdigest(),
                "answer": answer,
            }
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
    if required:
        raise ApiError(409, "현재 퀴즈를 저장할 수 없습니다. 최신 수업을 확인해주세요.")
    return None


def integer(data, name, minimum=0, optional=False):
    if optional and name not in data:
        return None
    value = data.get(name)
    if type(value) is not int or not minimum <= value <= 9007199254740991:
        raise ApiError(400, name + " 항목을 확인해주세요.")
    return value


class LessonLearningService:
    def __init__(self, app):
        self.app = app

    def context(self, c, user, cid, lid, record=False):
        user = active_member(c, user)
        if record and not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        course = c.execute(
            "SELECT * FROM courses WHERE id=? AND status IN ('published','archived')",
            (cid,),
        ).fetchone()
        lesson = c.execute(
            "SELECT * FROM course_lessons WHERE course_id=? AND id=? AND status='published'",
            (cid, lid),
        ).fetchone()
        if not course or not lesson:
            raise ApiError(404, "현재 학습할 수 있는 차시를 찾을 수 없습니다.")
        access = for_member(c, user["id"] if user else "", cid)
        if record:
            if not access["canAccess"]:
                raise ApiError(403, denial(access))
        elif (
            not (lesson["free"] and course["status"] == "published")
            and not access["canAccess"]
            and not can_manage_course(c, user, course)
        ):
            if not user:
                raise ApiError(401, "로그인 후 이용해주세요.")
            raise ApiError(403, denial(access))
        return user, course, lesson, access

    def completion(self, c, user, cid, lid):
        if not user:
            return {
                "completed": False,
                "version": 0,
                "completedAt": None,
                "source": "none",
                "lessonVersion": None,
                "eventCount": 0,
            }
        args = (user["id"], cid, lid)
        progress = c.execute(
            "SELECT completed_at FROM progress WHERE user_id=? AND course_id=? AND lesson_id=?",
            args,
        ).fetchone()
        tally = c.execute(
            "SELECT COALESCE(MAX(result_version),0),COUNT(*) FROM lesson_progress_events WHERE user_id=? AND course_id=? AND lesson_id=?",
            args,
        ).fetchone()
        event = c.execute(
            "SELECT * FROM lesson_progress_events WHERE user_id=? AND course_id=? AND lesson_id=? AND changed=1 ORDER BY result_version DESC,rowid DESC LIMIT 1",
            args,
        ).fetchone()
        recorded = bool(
            progress
            and event
            and event["completed"]
            and event["completed_at"] == progress["completed_at"]
        )
        return {
            "completed": bool(progress),
            "version": tally[0],
            "completedAt": progress["completed_at"] if progress else None,
            "source": "recorded" if recorded else "legacy" if progress else "none",
            "lessonVersion": event["lesson_version"] if recorded else None,
            "eventCount": tally[1],
        }

    def snapshot(self, c, context):
        user, course, lesson, access = context
        cid, lid = course["id"], lesson["id"]
        quiz = quiz_version(lesson)
        total = (
            c.execute(
                "SELECT COUNT(*) FROM lesson_quiz_attempts WHERE user_id=? AND course_id=? AND lesson_id=?",
                (user["id"], cid, lid),
            ).fetchone()[0]
            if user and access["canAccess"]
            else None
        )
        return {
            "courseId": cid,
            "lessonId": lid,
            "lessonVersion": lesson["version"],
            "canRecord": bool(user and access["canAccess"]),
            "accessStatus": access["status"],
            "quizHash": quiz["hash"] if quiz else None,
            "quizAvailable": bool(quiz),
            "quizAttemptCount": total,
            "completion": self.completion(c, user, cid, lid),
        }

    def attach(self, c, user, cid, lid, expected):
        context = self.context(c, user, cid, lid)
        if context[2]["version"] != expected["version"]:
            raise ApiError(409, "수업 내용이 변경됐습니다. 최신 수업을 확인해주세요.")
        return self.snapshot(c, context)

    def read(self, user, cid, lid):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            return {"learning": self.snapshot(c, self.context(c, user, cid, lid))}

    def request(self, data, kind):
        allowed = (
            {"requestKey", "lessonVersion", "quizHash", "answer"}
            if kind == "quiz"
            else {
                "courseId",
                "lessonId",
                "completed",
                "requestKey",
                "lessonVersion",
                "completionVersion",
            }
        )
        if not isinstance(data, dict) or set(data) - allowed:
            raise ApiError(400, "허용된 학습 요청 항목을 확인해주세요.")
        key = (
            text_field(data, "requestKey", minimum=8, maximum=120)
            if "requestKey" in data
            else new_id("legacy") if kind == "completion" else None
        )
        if not key or not re.fullmatch(r"[A-Za-z0-9_-]{8,120}", key):
            raise ApiError(400, "저장 요청 식별자를 확인해주세요.")
        if kind == "quiz":
            qhash = data.get("quizHash")
            if not isinstance(qhash, str) or not re.fullmatch(r"[a-f0-9]{64}", qhash):
                raise ApiError(400, "퀴즈 판본을 확인해주세요.")
            answer = integer(data, "answer")
            if answer > 5:
                raise ApiError(400, "답안을 확인해주세요.")
            body = {
                "answer": answer,
                "quizHash": qhash,
                "lessonVersion": integer(data, "lessonVersion", 1),
            }
        else:
            if type(data.get("completed")) is not bool:
                raise ApiError(400, "완료 여부를 확인해주세요.")
            # An identified client must supply both optimistic versions. Legacy
            # clients without a key retain their original current-state API.
            identified = "requestKey" in data
            body = {
                "completed": data["completed"],
                "lessonVersion": integer(
                    data, "lessonVersion", 1, optional=not identified
                ),
                "completionVersion": integer(
                    data, "completionVersion", optional=not identified
                ),
            }
        return key, body, digest(body)

    def receipt(self, c, kind, uid, cid, lid, key, request_hash):
        row = c.execute(
            f"SELECT * FROM {TABLES[kind]} WHERE user_id=? AND course_id=? AND lesson_id=? AND request_key=?",
            (uid, cid, lid, key),
        ).fetchone()
        if row and row["request_hash"] != request_hash:
            raise ApiError(
                409, "같은 저장 요청의 내용이 다릅니다. 현재 기록을 확인해주세요."
            )
        return row

    def validate_new(self, c, context, kind, body):
        user, course, lesson, _ = context
        if (
            body["lessonVersion"] is not None
            and body["lessonVersion"] != lesson["version"]
        ):
            raise ApiError(409, "수업 내용이 변경됐습니다. 최신 수업을 확인해주세요.")
        if kind == "quiz":
            quiz = quiz_version(lesson, required=True)
            if body["quizHash"] != quiz["hash"]:
                raise ApiError(409, "퀴즈가 변경됐습니다. 최신 수업을 확인해주세요.")
            if body["answer"] >= len(quiz["quiz"]["options"]):
                raise ApiError(400, "답안을 확인해주세요.")
        elif (
            body["completionVersion"] is not None
            and body["completionVersion"]
            != self.completion(c, user, course["id"], lesson["id"])["version"]
        ):
            raise ApiError(
                409, "다른 창에서 완료 상태를 변경했습니다. 현재 기록을 확인해주세요."
            )

    def attempt_dict(self, c, row, current):
        version = c.execute(
            "SELECT quiz_json FROM lesson_quiz_versions WHERE course_id=? AND lesson_id=? AND quiz_hash=?",
            (row["course_id"], row["lesson_id"], row["quiz_hash"]),
        ).fetchone()
        quiz = quiz_version(current)
        return {
            "id": row["id"],
            "requestKey": row["request_key"],
            "lessonVersion": row["lesson_version"],
            "quizHash": row["quiz_hash"],
            "answer": row["answer"],
            "correct": bool(row["correct"]),
            "quiz": json.loads(version["quiz_json"]),
            "createdAt": row["created_at"],
            "currentQuiz": bool(quiz and quiz["hash"] == row["quiz_hash"]),
        }

    def event_dict(self, row):
        return {
            "id": row["id"],
            "requestKey": row["request_key"],
            "completed": bool(row["completed"]),
            "changed": bool(row["changed"]),
            "resultVersion": row["result_version"],
            "lessonVersion": row["lesson_version"],
            "completedAt": row["completed_at"],
            "createdAt": row["created_at"],
        }

    def result(self, c, kind, row, context, replayed):
        learning = self.snapshot(c, context)
        if kind == "quiz":
            return {
                "attempt": self.attempt_dict(c, row, context[2]),
                "learning": learning,
                "replayed": replayed,
            }
        user, course, _, _ = context
        return {
            "receipt": self.event_dict(row),
            "learning": learning,
            "enrollment": self.app.enrollment(c, user["id"], course["id"]),
            "replayed": replayed,
        }

    def write(self, user, cid, lid, data, kind):
        # Check authorization before even returning an old private receipt. The
        # second check under BEGIN IMMEDIATE covers role/grant/version races.
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            context = self.context(c, user, cid, lid, record=True)
            user = context[0]
            key, body, request_hash = self.request(data, kind)
            row = self.receipt(c, kind, user["id"], cid, lid, key, request_hash)
            if row:
                return self.result(c, kind, row, context, True)
            self.validate_new(c, context, kind, body)
        self.app.limiter.check(("lesson-" + kind, user["id"]), 120, 3600)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            context = self.context(c, user, cid, lid, record=True)
            row = self.receipt(c, kind, user["id"], cid, lid, key, request_hash)
            if row:
                return self.result(c, kind, row, context, True)
            self.validate_new(c, context, kind, body)
            lesson = context[2]
            at = now_iso()
            rid = new_id("attempt" if kind == "quiz" else "completion")
            if kind == "quiz":
                quiz = quiz_version(lesson, required=True)
                c.execute(
                    "INSERT OR IGNORE INTO lesson_quiz_versions(course_id,lesson_id,quiz_hash,captured_lesson_version,quiz_json,created_at) VALUES(?,?,?,?,?,?)",
                    (cid, lid, quiz["hash"], lesson["version"], quiz["json"], at),
                )
                stored = c.execute(
                    "SELECT quiz_json FROM lesson_quiz_versions WHERE course_id=? AND lesson_id=? AND quiz_hash=?",
                    (cid, lid, quiz["hash"]),
                ).fetchone()
                if stored[0] != quiz["json"]:
                    raise ApiError(409, "퀴즈 판본을 확인할 수 없습니다.")
                c.execute(
                    "INSERT INTO lesson_quiz_attempts(id,user_id,course_id,lesson_id,quiz_hash,lesson_version,answer,correct,request_key,request_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        rid,
                        user["id"],
                        cid,
                        lid,
                        quiz["hash"],
                        lesson["version"],
                        body["answer"],
                        int(body["answer"] == quiz["answer"]),
                        key,
                        request_hash,
                        at,
                    ),
                )
            else:
                current = self.completion(c, user, cid, lid)
                changed = current["completed"] != body["completed"]
                result_version = current["version"] + int(changed)
                completed_at = (
                    current["completedAt"]
                    if body["completed"] and current["completed"]
                    else at if body["completed"] else None
                )
                if changed and body["completed"]:
                    c.execute(
                        "INSERT INTO progress(user_id,course_id,lesson_id,completed_at) VALUES(?,?,?,?)",
                        (user["id"], cid, lid, completed_at),
                    )
                elif changed:
                    c.execute(
                        "DELETE FROM progress WHERE user_id=? AND course_id=? AND lesson_id=?",
                        (user["id"], cid, lid),
                    )
                c.execute(
                    "INSERT INTO lesson_progress_events(id,user_id,course_id,lesson_id,request_key,request_hash,completed,changed,result_version,lesson_version,completed_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        rid,
                        user["id"],
                        cid,
                        lid,
                        key,
                        request_hash,
                        int(body["completed"]),
                        int(changed),
                        result_version,
                        lesson["version"],
                        completed_at,
                        at,
                    ),
                )
            audit(
                c,
                user,
                "lesson." + kind,
                "lesson",
                lid,
                {"courseId": cid, "receiptId": rid, "lessonVersion": lesson["version"]},
            )
            row = c.execute(
                f"SELECT * FROM {TABLES[kind]} WHERE id=?", (rid,)
            ).fetchone()
            return self.result(c, kind, row, context, False)

    def progress(self, user, data):
        cid = text_field(data, "courseId", maximum=100)
        lid = text_field(data, "lessonId", maximum=100)
        return self.write(user, cid, lid, data, "completion")

    def history(self, user, cid, lid, query, kind):
        page, size, offset = page_args(query)
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            context = self.context(c, user, cid, lid, record=True)
            args = (context[0]["id"], cid, lid)
            through = query.get("through", [None])[0]
            if through is not None and (
                not isinstance(through, str) or len(through) > 100
            ):
                raise ApiError(400, "조회 범위를 확인해주세요.")
            # An own receipt ID freezes the page window without exposing other
            # members' global row numbers or changing when new records arrive.
            bound = c.execute(
                f"SELECT rowid,id FROM {TABLES[kind]} WHERE user_id=? AND course_id=? AND lesson_id=?"
                + (
                    " AND id=?"
                    if through is not None
                    else " ORDER BY rowid DESC LIMIT 1"
                ),
                args + ((through,) if through is not None else ()),
            ).fetchone()
            if through is not None and not bound:
                raise ApiError(404, "해당 조회 범위를 찾을 수 없습니다.")
            ceiling = bound["rowid"] if bound else 0
            total = c.execute(
                f"SELECT COUNT(*) FROM {TABLES[kind]} WHERE user_id=? AND course_id=? AND lesson_id=? AND rowid<=?",
                args + (ceiling,),
            ).fetchone()[0]
            rows = c.execute(
                f"SELECT * FROM {TABLES[kind]} WHERE user_id=? AND course_id=? AND lesson_id=? AND rowid<=? ORDER BY rowid DESC LIMIT ? OFFSET ?",
                args + (ceiling, size, offset),
            ).fetchall()
            return {
                "items": [
                    (
                        self.attempt_dict(c, row, context[2])
                        if kind == "quiz"
                        else self.event_dict(row)
                    )
                    for row in rows
                ],
                "page": page,
                "pageSize": size,
                "total": total,
                "hasMore": offset + size < total,
                "through": bound["id"] if bound else None,
                "learning": self.snapshot(c, context),
            }
