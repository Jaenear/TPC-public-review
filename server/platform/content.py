"""Database catalogue, instructor publishing rules and private video delivery."""

import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote, unquote
from ..catalog import COURSES, LESSONS
from .profiles import public_coaches, public_profile
from .pricing import select_price, managed
from .resources import normalize_refs
from ..core import ApiError, text_field, choice_field, now_iso, new_id
from .history import lesson_snapshot, record_history
from .common import (
    audit,
    can_manage_course,
    has_access,
    require_role,
    row_dict,
    version,
)


class CatalogStore:
    def __init__(self, db, mode=None):
        self.db = db
        self.mode = mode or (lambda: "test")
        with db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if not c.execute("SELECT 1 FROM courses LIMIT 1").fetchone():
                for course in COURSES:
                    c.execute(
                        "INSERT INTO courses(id,title,subtitle,description,category,level,price,duration,coach_id,accent,art,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,'published',?,?)",
                        tuple(
                            course[k]
                            for k in (
                                "id",
                                "title",
                                "subtitle",
                                "description",
                                "category",
                                "level",
                                "price",
                                "duration",
                                "coachId",
                                "accent",
                                "art",
                            )
                        )
                        + (now_iso(), now_iso()),
                    )
                    section = "section-" + course["id"]
                    c.execute(
                        "INSERT INTO course_sections(id,course_id,title) VALUES(?,?,?)",
                        (section, course["id"], "기본 커리큘럼"),
                    )
                    for index, lesson in enumerate(LESSONS[course["id"]]):
                        c.execute(
                            "INSERT INTO course_lessons(course_id,id,section_id,title,duration,free,content,takeaway,quiz,status,sort_order,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'published',?,?,?)",
                            (
                                course["id"],
                                lesson["id"],
                                section,
                                lesson["title"],
                                lesson["duration"],
                                int(lesson["free"]),
                                json.dumps(lesson["content"], ensure_ascii=False),
                                lesson["takeaway"],
                                json.dumps(lesson["quiz"], ensure_ascii=False),
                                index,
                                now_iso(),
                                now_iso(),
                            ),
                        )

            for row in c.execute("SELECT * FROM course_lessons"):
                record_history(c, row)

    def public_catalog(self, user=None):
        with self.db.connect() as c:
            rows = c.execute(
                "SELECT * FROM courses WHERE status='published' OR (status='archived' AND ? IS NOT NULL AND EXISTS(SELECT 1 FROM enrollments e WHERE e.course_id=courses.id AND e.user_id=?)) ORDER BY created_at,id",
                (user["id"] if user else None, user["id"] if user else None),
            ).fetchall()
            courses = [self.course_dict(c, row) for row in rows]
            coaches = public_coaches(c)
        # Preserve the curated order for existing course IDs.
        order = {item["id"]: i for i, item in enumerate(COURSES)}
        courses.sort(key=lambda item: order.get(item["id"], len(order)))
        return {"courses": courses, "coaches": coaches}

    def course_dict(self, c, row, private=False):
        item = row_dict(row)
        item["pilot"] = bool(row["pilot"])
        quote = select_price(c, row, self.mode())
        if not private:
            if not row["pilot"] and quote["ready"]:
                from .policies import conditions_bundle

                quote["purchaseReady"] = conditions_bundle(
                    c, "checkout", self.mode(), row["id"]
                )["ready"]
            else:
                quote["purchaseReady"] = quote["ready"]
            item["price"] = quote["amount"]
        item["pricing"] = quote
        instructor = (
            c.execute(
                "SELECT name FROM users WHERE id=?", (row["instructor_user_id"],)
            ).fetchone()
            if row["instructor_user_id"]
            else None
        )
        item["instructorName"] = instructor["name"] if instructor else None
        if row["instructor_user_id"] and not private:
            profile = c.execute(
                "SELECT * FROM coach_profiles WHERE owner_user_id=?",
                (row["instructor_user_id"],),
            ).fetchone()
            coach = public_profile(c, profile)
            item["coachId"] = coach["id"] if coach else None
            item["instructorName"] = coach["name"] if coach else "TPC 콘텐츠팀"
        elif not private:
            legacy = c.execute(
                "SELECT owner_user_id FROM coach_profiles WHERE id=?",
                (row["coach_id"],),
            ).fetchone()
            if legacy and legacy["owner_user_id"]:
                item["coachId"] = None
                item["instructorName"] = "TPC 콘텐츠팀"
        lessons = c.execute(
            "SELECT l.* FROM course_lessons l JOIN course_sections s ON s.id=l.section_id AND s.course_id=l.course_id WHERE l.course_id=? "
            + ("" if private else "AND l.status='published' ")
            + "ORDER BY s.sort_order,s.id,l.sort_order,l.id",
            (row["id"],),
        ).fetchall()
        item["format"] = (
            "영상·텍스트 클래스"
            if any(lesson["video_asset_id"] for lesson in lessons)
            else "텍스트 클래스"
        )
        item["lessons"] = [
            (
                self.lesson_dict(lesson, private=private)
                if private
                else {
                    **{key: lesson[key] for key in ("id", "title", "duration")},
                    "free": bool(lesson["free"]),
                    "sectionId": lesson["section_id"],
                    "hasVideo": bool(lesson["video_asset_id"]),
                }
            )
            for lesson in lessons
        ]
        item["sections"] = [
            row_dict(section)
            for section in c.execute(
                "SELECT * FROM course_sections WHERE course_id=? ORDER BY sort_order,id",
                (row["id"],),
            )
        ]
        if private:
            item["learningResources"] = [
                {
                    "id": r["id"],
                    "filename": r["filename"],
                    "mimeType": r["mime_type"],
                    "size": r["size"],
                    "status": r["status"],
                }
                for r in c.execute(
                    "SELECT id,filename,mime_type,size,status FROM learning_resources WHERE course_id=? ORDER BY created_at DESC,rowid DESC",
                    (row["id"],),
                )
            ]
            item["assets"] = [
                row_dict(asset)
                for asset in c.execute(
                    "SELECT id,filename,size,created_at FROM media_assets WHERE course_id=? ORDER BY created_at DESC",
                    (row["id"],),
                )
            ]
        return item

    def lesson_dict(self, row, private=True):
        return lesson_snapshot(row)

    def find_course(self, course_id):
        with self.db.connect() as c:
            row = c.execute(
                "SELECT * FROM courses WHERE id=? AND status IN ('published','archived')",
                (course_id,),
            ).fetchone()
            return self.course_dict(c, row) if row else None

    def find_lesson(self, course_id, lesson_id):
        with self.db.connect() as c:
            row = c.execute(
                "SELECT l.* FROM course_lessons l JOIN courses c ON c.id=l.course_id WHERE l.course_id=? AND l.id=? AND c.status IN ('published','archived') AND l.status='published'",
                (course_id, lesson_id),
            ).fetchone()
            return self.lesson_dict(row) if row else None


class ContentService:
    def __init__(self, app):
        self.app = app
        self.media_dir = Path(app.db.path).parent / (Path(app.db.path).stem + "-media")
        self.media_dir.mkdir(exist_ok=True, mode=0o700)

    def managed_course(self, c, user, course_id):
        row = c.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        if not row:
            raise ApiError(404, "강의를 찾을 수 없습니다.")
        if not can_manage_course(c, user, row):
            raise ApiError(403, "담당 강의만 관리할 수 있습니다.")
        return row

    def courses(self, user, course_id=None):
        with self.app.db.connect() as c:
            require_role(c, user, "coach")
            if course_id:
                return {
                    "course": self.app.catalog.course_dict(
                        c, self.managed_course(c, user, course_id), private=True
                    )
                }
            rows = c.execute(
                "SELECT * FROM courses "
                + ("" if user["role"] == "admin" else "WHERE instructor_user_id=? ")
                + "ORDER BY updated_at DESC",
                () if user["role"] == "admin" else (user["id"],),
            ).fetchall()
            return {
                "courses": [
                    self.app.catalog.course_dict(c, row, private=True) for row in rows
                ]
            }

    def save_course(self, user, data, course_id=None):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            require_role(c, user, "coach")
            old = self.managed_course(c, user, course_id) if course_id else None
            if old:
                version(data, old)
                if old["status"] == "published" and user["role"] != "admin":
                    raise ApiError(403, "발행된 강의 변경은 관리자 검수가 필요합니다.")
            title = text_field(data, "title", minimum=2, maximum=100)
            subtitle = text_field(data, "subtitle", maximum=200, optional=True)
            description = text_field(data, "description", minimum=10, maximum=10000)
            category = choice_field(
                data,
                "category",
                ("프리플랍", "라이브 캐시", "토너먼트", "멘탈", "기타"),
            )
            level = choice_field(data, "level", ("입문", "중급", "심화"))
            price = data.get("price")
            if type(price) is not int or not 0 <= price <= 10000000:
                raise ApiError(400, "가격은 0~10,000,000원의 정수로 입력해주세요.")
            status = data.get("status", old["status"] if old else "draft")
            if status not in ("draft", "review", "published", "archived"):
                raise ApiError(400, "발행 상태를 확인해주세요.")
            if (
                old
                and old["status"] in ("published", "archived")
                and status in ("draft", "review")
                and c.execute(
                    "SELECT 1 FROM enrollments WHERE course_id=? LIMIT 1", (course_id,)
                ).fetchone()
            ):
                raise ApiError(
                    409,
                    "수강 이력이 있는 강의는 발행 또는 보관 상태로 유지해주세요. 신규 판매를 중단하려면 보관을 선택해주세요.",
                )
            if status in ("published", "archived") and user["role"] != "admin":
                raise ApiError(403, "강의 발행과 보관은 관리자만 처리할 수 있습니다.")
            if old and old["status"] != status and status == "review":
                raise ApiError(
                    409,
                    "품질 검수 화면에서 자체 점검과 근거를 저장한 뒤 검수를 요청해주세요.",
                )
            instructor = (
                data.get(
                    "instructorUserId", old["instructor_user_id"] if old else user["id"]
                )
                or None
            )
            if user["role"] != "admin" and instructor != user["id"]:
                raise ApiError(403, "다른 코치로 담당자를 변경할 수 없습니다.")
            if (
                instructor
                and not c.execute(
                    "SELECT 1 FROM users u WHERE u.id=? AND u.status='active' AND (u.role='admin' OR EXISTS(SELECT 1 FROM user_roles r WHERE r.user_id=u.id AND r.role='coach'))",
                    (instructor,),
                ).fetchone()
            ):
                raise ApiError(400, "활성 코치 계정을 지정해주세요.")
            pilot = data.get("pilot", bool(old["pilot"]) if old else True)
            if type(pilot) is not bool:
                raise ApiError(400, "파일럿 여부를 확인해주세요.")
            if user["role"] != "admin" and not pilot:
                raise ApiError(403, "정식 판매 설정은 관리자가 검수해야 합니다.")
            if status == "published" and not pilot and not instructor:
                raise ApiError(400, "정식 강의 발행에는 활성 담당 코치가 필요합니다.")
            timestamp = now_iso()
            if not old:
                course_id = new_id("course").replace("_", "-")
                c.execute(
                    "INSERT INTO courses(id,title,subtitle,description,category,level,price,duration,coach_id,instructor_user_id,pilot,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        course_id,
                        title,
                        subtitle,
                        description,
                        category,
                        level,
                        price,
                        "준비 중",
                        "strategy-coach",
                        instructor,
                        int(pilot),
                        timestamp,
                        timestamp,
                    ),
                )
                c.execute(
                    "INSERT INTO course_sections(id,course_id,title) VALUES(?,?,?)",
                    (new_id("section"), course_id, "커리큘럼"),
                )
                if status != "draft":
                    raise ApiError(400, "새 강의는 초안으로 생성합니다.")
            else:
                if status == "published":
                    lessons = c.execute(
                        "SELECT * FROM course_lessons WHERE course_id=? AND status='published'",
                        (course_id,),
                    ).fetchall()
                    if not lessons or any(
                        not json.loads(l["content"]) and not l["video_asset_id"]
                        for l in lessons
                    ):
                        raise ApiError(
                            400,
                            "내용 또는 영상이 있는 공개 차시가 한 개 이상 필요합니다.",
                        )
                    for lesson in lessons:
                        normalize_refs(
                            c,
                            course_id,
                            json.loads(lesson["resources"]),
                            published=True,
                        )
                    if old["status"] != "published":
                        raise ApiError(
                            409, "품질 체크리스트의 관리자 검수를 통해 발행해주세요."
                        )
                c.execute(
                    "UPDATE courses SET title=?,subtitle=?,description=?,category=?,level=?,price=?,status=?,instructor_user_id=?,pilot=?,version=version+1,updated_at=? WHERE id=?",
                    (
                        title,
                        subtitle,
                        description,
                        category,
                        level,
                        price,
                        status,
                        instructor,
                        int(pilot),
                        timestamp,
                        course_id,
                    ),
                )
            audit(
                c,
                user,
                "course.updated" if old else "course.created",
                "course",
                course_id,
                {"status": status},
            )
            return {
                "course": self.app.catalog.course_dict(
                    c,
                    c.execute(
                        "SELECT * FROM courses WHERE id=?", (course_id,)
                    ).fetchone(),
                    private=True,
                )
            }

    def validate_publication(self, c, course):
        """Final technical gate shared with the version-bound quality review."""
        if not course["pilot"]:
            from .contracts import qualified

            if not qualified(c, course["instructor_user_id"]):
                raise ApiError(
                    409, "정식 강의 발행에는 현재 활성 담당 코치가 필요합니다."
                )
        rows = c.execute(
            "SELECT * FROM course_lessons WHERE course_id=? AND status='published'",
            (course["id"],),
        ).fetchall()
        if not rows:
            raise ApiError(409, "공개할 차시가 한 개 이상 필요합니다.")
        for row in rows:
            self.prepare_lesson(c, course, lesson_snapshot(row), row)

    def save_lesson(self, user, course_id, data, lesson_id=None):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            return self.write_lesson(c, user, course_id, data, lesson_id)

    def prepare_lesson(self, c, course, data, old=None):
        """Validate draft proposals and direct edits through the same rules."""
        course_id = course["id"]
        title = text_field(data, "title", minimum=2, maximum=120)
        duration = text_field(data, "duration", maximum=40, optional=True)
        if data.get("sectionId") is not None and not isinstance(data["sectionId"], str):
            raise ApiError(400, "섹션 식별자를 확인해주세요.")
        section_id = (
            data.get("sectionId")
            or c.execute(
                "SELECT id FROM course_sections WHERE course_id=? ORDER BY sort_order LIMIT 1",
                (course_id,),
            ).fetchone()[0]
        )
        if not c.execute(
            "SELECT 1 FROM course_sections WHERE id=? AND course_id=?",
            (section_id, course_id),
        ).fetchone():
            raise ApiError(400, "이 강의의 섹션을 선택해주세요.")
        content = data.get("content", [])
        if not isinstance(content, list) or len(content) > 30:
            raise ApiError(400, "차시 본문을 확인해주세요.")
        blocks = []
        for block in content:
            if not isinstance(block, dict):
                raise ApiError(400, "본문 형식을 확인해주세요.")
            blocks.append(
                {
                    "heading": text_field(block, "heading", minimum=1, maximum=160),
                    "text": text_field(block, "text", minimum=1, maximum=20000),
                }
            )
        if len(json.dumps(blocks).encode()) > 50000:
            raise ApiError(400, "본문은 50KB 이내로 입력해주세요.")
        free = data.get("free", False)
        if type(free) is not bool:
            raise ApiError(400, "미리보기 여부를 확인해주세요.")
        status = choice_field(data, "status", ("draft", "published", "archived"))
        if (
            old
            and old["status"] == "published"
            and status != "published"
            and course["status"] in ("published", "archived")
            and c.execute(
                "SELECT COUNT(*) FROM course_lessons WHERE course_id=? AND status='published'",
                (course_id,),
            ).fetchone()[0]
            <= 1
        ):
            raise ApiError(
                409,
                "수강 가능한 강의에는 공개 차시가 필요합니다. 마지막 공개 차시는 숨길 수 없습니다. 신규 판매를 중단하려면 강의를 보관해주세요.",
            )
        if data.get("videoAssetId") is not None and not isinstance(
            data["videoAssetId"], str
        ):
            raise ApiError(400, "영상 식별자를 확인해주세요.")
        asset = data.get("videoAssetId") or None
        if (
            asset
            and not c.execute(
                "SELECT 1 FROM media_assets WHERE id=? AND course_id=?",
                (asset, course_id),
            ).fetchone()
        ):
            raise ApiError(400, "이 강의에 업로드된 영상을 선택해주세요.")
        if status == "published" and not blocks and not asset:
            raise ApiError(400, "공개 차시에는 본문 또는 영상이 필요합니다.")
        quiz = data.get("quiz")
        if quiz is not None:
            if (
                not isinstance(quiz, dict)
                or not isinstance(quiz.get("options"), list)
                or not 2 <= len(quiz["options"]) <= 6
            ):
                raise ApiError(400, "퀴즈 선택지는 2~6개가 필요합니다.")
            quiz = {
                "question": text_field(quiz, "question", maximum=500),
                "options": [
                    text_field({"value": v}, "value", maximum=500)
                    for v in quiz["options"]
                ],
                "answer": quiz.get("answer"),
                "explanation": text_field(quiz, "explanation", maximum=2000),
            }
            if type(quiz["answer"]) is not int or not 0 <= quiz["answer"] < len(
                quiz["options"]
            ):
                raise ApiError(400, "퀴즈 정답을 확인해주세요.")
        order = data.get(
            "sortOrder",
            (
                old["sort_order"]
                if old
                else c.execute(
                    "SELECT COUNT(*) FROM course_lessons WHERE course_id=?",
                    (course_id,),
                ).fetchone()[0]
            ),
        )
        if type(order) is not int or not 0 <= order <= 10000:
            raise ApiError(400, "차시 순서를 확인해주세요.")
        takeaway = text_field(data, "takeaway", maximum=2000, optional=True)
        resources = normalize_refs(
            c,
            course_id,
            data.get("resources", json.loads(old["resources"]) if old else []),
            published=status == "published",
        )
        return {
            "title": title,
            "duration": duration,
            "sectionId": section_id,
            "sortOrder": order,
            "status": status,
            "free": free,
            "content": blocks,
            "takeaway": takeaway,
            "quiz": quiz,
            "videoAssetId": asset,
            "resources": resources,
        }

    def write_lesson(
        self,
        c,
        user,
        course_id,
        data,
        lesson_id=None,
        source="direct",
        reason="",
        revision_id=None,
    ):
        """Apply a lesson inside the caller's transaction, including history."""
        course = self.managed_course(c, user, course_id)
        if course["status"] in ("published", "archived") and user["role"] != "admin":
            raise ApiError(403, "발행 강의의 차시 변경은 관리자 검수가 필요합니다.")
        old = (
            c.execute(
                "SELECT * FROM course_lessons WHERE course_id=? AND id=?",
                (course_id, lesson_id),
            ).fetchone()
            if lesson_id
            else None
        )
        if lesson_id and not old:
            raise ApiError(404, "차시를 찾을 수 없습니다.")
        if old:
            version(data, old)
        prepared = self.prepare_lesson(c, course, data, old)
        if old:
            record_history(c, old)
        values = (
            prepared["sectionId"],
            prepared["title"],
            prepared["duration"],
            int(prepared["free"]),
            json.dumps(prepared["content"], ensure_ascii=False),
            prepared["takeaway"],
            (
                json.dumps(prepared["quiz"], ensure_ascii=False)
                if prepared["quiz"]
                else None
            ),
            prepared["videoAssetId"],
            json.dumps(prepared["resources"], ensure_ascii=False),
            prepared["status"],
            prepared["sortOrder"],
            now_iso(),
        )
        if old:
            c.execute(
                "UPDATE course_lessons SET section_id=?,title=?,duration=?,free=?,content=?,takeaway=?,quiz=?,video_asset_id=?,resources=?,status=?,sort_order=?,updated_at=?,version=version+1 WHERE course_id=? AND id=?",
                values + (course_id, lesson_id),
            )
        else:
            lesson_id = new_id("lesson").replace("_", "-")
            c.execute(
                "INSERT INTO course_lessons(section_id,title,duration,free,content,takeaway,quiz,video_asset_id,resources,status,sort_order,updated_at,course_id,id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                values + (course_id, lesson_id, now_iso()),
            )
        c.execute(
            "UPDATE courses SET version=version+1,updated_at=? WHERE id=?",
            (now_iso(), course_id),
        )
        durations = [
            row[0]
            for row in c.execute(
                "SELECT duration FROM course_lessons WHERE course_id=? AND status='published'",
                (course_id,),
            )
        ]
        minutes = [
            int(match.group(1))
            for value in durations
            if (match := re.fullmatch(r"(\d+)\s*분", value.strip()))
        ]
        if durations:
            label = (
                "약 {}분".format(sum(minutes))
                if len(minutes) == len(durations)
                else "{}개 차시".format(len(durations))
            )
            c.execute("UPDATE courses SET duration=? WHERE id=?", (label, course_id))
        row = c.execute(
            "SELECT * FROM course_lessons WHERE course_id=? AND id=?",
            (course_id, lesson_id),
        ).fetchone()
        record_history(c, row, user, source, reason, revision_id)
        audit(
            c,
            user,
            "lesson.updated" if old else "lesson.created",
            "lesson",
            course_id + "/" + lesson_id,
        )
        return {"lesson": self.app.catalog.lesson_dict(row)}

    def add_section(self, user, course_id, data):
        title = text_field(data, "title", minimum=2, maximum=120)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            course = self.managed_course(c, user, course_id)
            version(data, course)
            if (
                course["status"] in ("published", "archived")
                and user["role"] != "admin"
            ):
                raise ApiError(403, "발행 강의의 구성 변경은 관리자 검수가 필요합니다.")
            section_id = new_id("section")
            order = c.execute(
                "SELECT COUNT(*) FROM course_sections WHERE course_id=?", (course_id,)
            ).fetchone()[0]
            c.execute(
                "INSERT INTO course_sections VALUES(?,?,?,?)",
                (section_id, course_id, title, order),
            )
            c.execute(
                "UPDATE courses SET version=version+1,updated_at=? WHERE id=?",
                (now_iso(), course_id),
            )
            audit(c, user, "section.created", "course", course_id)
            return {
                "section": {
                    "id": section_id,
                    "courseId": course_id,
                    "title": title,
                    "sortOrder": order,
                }
            }

    def upload(self, handler, user, course_id):
        with self.app.db.connect() as c:
            user, _ = managed(c, user, course_id)
        try:
            size = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(400, "파일 크기를 확인해주세요.")
        if not 32 <= size <= 100 * 1024 * 1024:
            raise ApiError(413, "MP4 영상은 100MB 이내로 업로드해주세요.")
        if handler.headers.get("Content-Type", "").split(";")[0] != "video/mp4":
            raise ApiError(415, "MP4 영상만 업로드할 수 있습니다.")
        name = Path(unquote(handler.headers.get("X-File-Name", "video.mp4"))).name[:150]
        handler.connection.settimeout(60)
        self.app.limiter.check(("media", user["id"]), 30, 3600)
        asset_id = new_id("media")
        target = self.media_dir / (asset_id + ".mp4")
        digest = hashlib.sha256()
        remaining = size
        try:
            first = handler.rfile.read(min(32, remaining))
            if len(first) < 12 or first[4:8] != b"ftyp":
                raise ApiError(400, "유효한 MP4 파일 헤더가 아닙니다.")
            with target.open("xb") as output:
                output.write(first)
                digest.update(first)
                remaining -= len(first)
                while remaining:
                    chunk = handler.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise ApiError(400, "파일 업로드가 완료되지 않았습니다.")
                    output.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            target.chmod(0o600)
            with self.app.db.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                user, _ = managed(c, user, course_id)
                c.execute(
                    "INSERT INTO media_assets(id,course_id,uploader_id,filename,size,sha256,created_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        asset_id,
                        course_id,
                        user["id"],
                        name,
                        size,
                        digest.hexdigest(),
                        now_iso(),
                    ),
                )
                audit(
                    c,
                    user,
                    "media.uploaded",
                    "course",
                    course_id,
                    {"assetId": asset_id, "size": size},
                )
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return {"asset": {"id": asset_id, "filename": name, "size": size}}

    def stream(self, h, user, asset_id):
        with self.app.db.connect() as c:
            asset = c.execute(
                "SELECT * FROM media_assets WHERE id=?", (asset_id,)
            ).fetchone()
            if not asset:
                raise ApiError(404, "영상을 찾을 수 없습니다.")
            course = c.execute(
                "SELECT * FROM courses WHERE id=?", (asset["course_id"],)
            ).fetchone()
            public = (
                c.execute(
                    "SELECT free FROM course_lessons WHERE course_id=? AND video_asset_id=? AND status='published'",
                    (asset["course_id"], asset_id),
                ).fetchall()
                if course["status"] in ("published", "archived")
                else []
            )
            if not can_manage_course(c, user, course) and not (
                public
                and (
                    (
                        course["status"] == "published"
                        and any(row["free"] for row in public)
                    )
                    or has_access(c, user, course["id"])
                )
            ):
                raise ApiError(403, "수강권이 필요한 영상입니다.")
        target = self.media_dir / (asset_id + ".mp4")
        if target.is_symlink() or not target.is_file():
            raise ApiError(404, "영상 파일을 찾을 수 없습니다.")
        size = target.stat().st_size
        if size != asset["size"]:
            raise ApiError(409, "영상 원본의 크기가 달라 재생을 중단했습니다.")
        start = 0
        end = size - 1
        status = 200
        request = h.headers.get("Range")
        if request:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", request)
            if not match or not any(match.groups()):
                raise ApiError(
                    416,
                    "지원하지 않는 영상 범위입니다.",
                    {"Content-Range": "bytes */{}".format(size)},
                )
            a, b = match.groups()
            if a:
                start = int(a)
                end = min(int(b), end) if b else end
            else:
                start = max(0, size - int(b))
            if start > end or start >= size:
                raise ApiError(
                    416,
                    "영상 범위를 확인해주세요.",
                    {"Content-Range": "bytes */{}".format(size)},
                )
            status = 206
        h.send_response(status)
        h.send_header("Content-Type", "video/mp4")
        h.send_header("Content-Length", str(end - start + 1))
        h.send_header("Accept-Ranges", "bytes")
        h.send_header("Cache-Control", "private, no-store")
        h.send_header(
            "Content-Disposition",
            "inline; filename*=UTF-8''" + quote(asset["filename"]),
        )
        if status == 206:
            h.send_header("Content-Range", "bytes {}-{}/{}".format(start, end, size))
        h.security_headers()
        h.end_headers()
        if h.command != "HEAD":
            with target.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        break
                    h.wfile.write(chunk)
                    remaining -= len(chunk)
