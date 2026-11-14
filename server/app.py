"""A small same-origin JSON API and explicitly allowlisted static file server."""

from datetime import date, datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit, parse_qs
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
import time
import uuid

from .catalog import find_coach, find_course, find_lesson, public_catalog
from .database import Database
from .rate_limit import RateLimiter
from .platform.profiles import require_inquiry, record_booking_profile, booking_routing
from .access import (
    ensure_enrollment,
    ensure_source_enrollment,
    grant,
    for_member,
    summary as access_summary,
    denial,
)
from .core import ApiError, text_field, choice_field, email_field, password_field
from .solver import UnavailableSolver
from .platform.content import CatalogStore
from .platform.router import Platform, HANDLED
from .platform.common import can_manage_course


ROOT = Path(__file__).resolve().parent.parent
SESSION_SECONDS = 60 * 60 * 24 * 7
PBKDF2_ITERATIONS = 310000
MAX_BODY_BYTES = 65536
SEOUL = timezone(timedelta(hours=9))
HAND_RANKS = "AKQJT98765432"
HAND_CLASSES = {
    a + b if i == j else a + b + "s" if i < j else b + a + "o"
    for i, a in enumerate(HAND_RANKS)
    for j, b in enumerate(HAND_RANKS)
}
PUBLIC_USER_FIELDS = ("id", "name", "email", "role")
CAMEL_FIELDS = {
    "user_id": "userId",
    "course_id": "courseId",
    "coach_id": "coachId",
    "preferred_date": "preferredDate",
    "preferred_time": "preferredTime",
    "game_type": "gameType",
    "hero_hand": "heroHand",
    "created_at": "createdAt",
    "updated_at": "updatedAt",
    "user_name": "userName",
    "user_email": "userEmail",
}


def now_iso():
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def new_id(prefix):
    return prefix + "_" + uuid.uuid4().hex


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), PBKDF2_ITERATIONS
    ).hex()
    return "pbkdf2_sha256${}${}${}".format(PBKDF2_ITERATIONS, salt, digest)


def verify_password(password, encoded):
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        result = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("ascii"), int(rounds)
        ).hex()
        return hmac.compare_digest(result, expected)
    except (ValueError, TypeError):
        return False


def public_user(row):
    return {key: row[key] for key in PUBLIC_USER_FIELDS} if row else None


def serialize(row):
    item = {CAMEL_FIELDS.get(key, key): row[key] for key in row.keys()}
    if "userName" in item:
        item["user"] = {
            "id": item["userId"],
            "name": item["userName"],
            "email": item["userEmail"],
        }
    if "selected" in item:
        item["selected"] = json.loads(item["selected"])
    if "answers" in item:
        item["answers"] = json.loads(item["answers"])
    return item


def parse_cards(value, allowed_counts, label):
    cleaned = re.sub(r"[\s,/|·]+", "", value)
    pattern = re.compile(r"(?:10|[2-9TJQKA])[shdc♠♥♦♣]", re.IGNORECASE)
    matches = list(pattern.finditer(cleaned))
    if (
        "".join(match.group() for match in matches) != cleaned
        or len(matches) not in allowed_counts
    ):
        raise ApiError(400, "{}는 As Kh처럼 카드와 무늬를 입력해주세요.".format(label))
    suits = {"♠": "s", "♥": "h", "♦": "d", "♣": "c"}
    result = []
    for match in matches:
        card = match.group()
        rank, suit = card[:-1].upper(), card[-1].lower()
        result.append(("T" if rank == "10" else rank) + suits.get(suit, suit))
    return result


class Application:
    def __init__(self, db_path, origins, solver=None, gateway=None, *, identity_keyring=None):
        self.db = Database(db_path)
        self.catalog = CatalogStore(
            self.db, lambda: self.platform.commerce.gateway.mode
        )
        self.platform = Platform(self, gateway, identity_keyring)
        self.origins = {origin.rstrip("/") for origin in origins}
        self.hosts = {urlsplit(origin).netloc.lower() for origin in self.origins}
        self.secure_cookies = any(
            origin.startswith("https://") for origin in self.origins
        )
        self.limiter = RateLimiter(self.db)
        self.solver = solver or UnavailableSolver()
        self.dummy_password = hash_password(secrets.token_urlsafe(24))

    def user_from_cookie(self, cookie_header):
        cookies = SimpleCookie()
        try:
            cookies.load(cookie_header or "")
        except Exception:
            return None
        cookie = cookies.get("tpc_session")
        if not cookie or len(cookie.value) > 256:
            return None
        digest = hashlib.sha256(cookie.value.encode("utf-8")).hexdigest()
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id WHERE token_hash=? AND expires_at>?",
                (digest, int(time.time())),
            ).fetchone()
        if row and row["status"] != "active":
            return None
        result = public_user(row)
        if result:
            with self.db.connect() as connection:
                result["roles"] = [
                    role[0]
                    for role in connection.execute(
                        "SELECT role FROM user_roles WHERE user_id=?", (row["id"],)
                    )
                ]
            result["status"] = row["status"]
            result["version"] = row["version"]
        return result

    def create_session(self, user_id, old_cookie=""):
        token = secrets.token_urlsafe(32)
        with self.db.connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE expires_at<=?", (int(time.time()),)
            )
            connection.execute(
                "INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    user_id,
                    int(time.time()) + SESSION_SECONDS,
                ),
            )
        # Rotate an existing browser session on every authentication event.
        self.delete_session(old_cookie)
        return self.session_cookie(token, SESSION_SECONDS)

    def delete_session(self, cookie_header):
        cookies = SimpleCookie()
        try:
            cookies.load(cookie_header or "")
            token = cookies.get("tpc_session")
        except Exception:
            token = None
        if token:
            with self.db.connect() as connection:
                connection.execute(
                    "DELETE FROM sessions WHERE token_hash=?",
                    (hashlib.sha256(token.value.encode()).hexdigest(),),
                )

    def session_cookie(self, token, seconds):
        cookie = "tpc_session={}; HttpOnly; SameSite=Lax; Path=/; Max-Age={}".format(
            token, seconds
        )
        if self.secure_cookies:
            cookie += "; Secure"
        return cookie

    def enroll(self, connection, user_id, course_id, source="free", source_id=""):
        ensure_source_enrollment(connection, user_id, course_id, source, source_id)
        return self.enrollment(connection, user_id, course_id)

    def enrollment(self, connection, user_id, course_id, include_revoked=False):
        row = connection.execute(
            "SELECT * FROM enrollments WHERE user_id=? AND course_id=?",
            (user_id, course_id),
        ).fetchone()
        if not row:
            return None
        access = access_summary(connection, row["id"])
        if not access["canAccess"] and not include_revoked:
            return None
        result = serialize(row)
        result["accessStatus"] = access["status"]
        result["canAccess"] = access["canAccess"]
        result["access"] = access
        result["completedLessons"] = [
            row[0]
            for row in connection.execute(
                "SELECT lesson_id FROM progress WHERE user_id=? AND course_id=? ORDER BY completed_at, lesson_id",
                (user_id, course_id),
            ).fetchall()
        ]
        return result

    def member_data(self, user_id):
        with self.db.connect() as connection:
            course_ids = connection.execute(
                "SELECT course_id FROM enrollments WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            result = {
                "enrollments": [
                    self.enrollment(connection, user_id, row[0], include_revoked=True)
                    for row in course_ids
                ]
            }
            member = connection.execute(
                "SELECT * FROM users WHERE id=?", (user_id,)
            ).fetchone()
            for enrollment in result["enrollments"]:
                enrollment["videoProgress"] = (
                    self.platform.watch_progress.member_course(
                        connection, member, enrollment["courseId"]
                    )
                )
            for table in (
                "bookings",
                "reviews",
                "orders",
                "study_notes",
                "practice_attempts",
            ):
                result[
                    {"study_notes": "studyNotes", "practice_attempts": "practice"}.get(
                        table, table
                    )
                ] = [
                    (
                        {**serialize(row), **booking_routing(connection, row)}
                        if table == "bookings"
                        else serialize(self.platform.hand_reviews.project_legacy(connection, row)) if table == "reviews" else serialize(row)
                    )
                    for row in connection.execute(
                        "SELECT * FROM {} WHERE user_id=? ORDER BY created_at DESC,id DESC".format(
                            table
                        ),
                        (user_id,),
                    ).fetchall()
                ]
        return result


class TPCHandler(BaseHTTPRequestHandler):
    server_version = "TPC"
    sys_version = ""

    @property
    def app(self):
        return self.server.application

    def log_message(self, fmt, *args):
        # No credentials, email addresses, or request bodies are recorded.
        logging.getLogger("tpc.http").info(
            "%s %s", self.command, self.path.split("?")[0]
        )

    def do_GET(self):
        self.dispatch()

    def do_HEAD(self):
        self.dispatch(head_only=True)

    def do_POST(self):
        self.dispatch()

    def do_PUT(self):
        self.dispatch()

    def do_PATCH(self):
        self.dispatch()

    def do_DELETE(self):
        self.dispatch()

    def do_OPTIONS(self):
        self.send_json({"error": "다른 출처의 요청은 허용되지 않습니다."}, 405)

    def dispatch(self, head_only=False):
        try:
            if self.headers.get("Host", "").lower() not in self.app.hosts:
                raise ApiError(403, "허용되지 않은 호스트입니다.")
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if path.startswith("/api/"):
                # The sole server-to-server exception authenticates exact bytes
                # by HMAC. All browser/member writes retain their Origin check.
                if path == "/api/webhooks/toss":
                    status, result, headers = self.app.platform.reconciliation_runtime.webhook(self)
                    self.send_json(result, status, headers, head_only)
                    return
                if self.command not in ("GET", "HEAD"):
                    self.check_origin()
                self.user = self.app.user_from_cookie(self.headers.get("Cookie"))
                expected_user = self.headers.get("X-TPC-User")
                if (
                    expected_user
                    and path != "/api/session"
                    and not path.startswith("/api/auth/")
                ):
                    if not self.user or expected_user != self.user["id"]:
                        raise ApiError(
                            401,
                            "로그인 상태가 변경되었습니다. 다시 로그인하고 내용을 확인해주세요.",
                        )
                try:
                    query = parse_qs(parsed.query, max_num_fields=100)
                except ValueError:
                    raise ApiError(400, "조회 항목이 너무 많거나 올바르지 않습니다.")
                response = self.app.platform.route(
                    self,
                    path,
                    "GET" if self.command == "HEAD" else self.command,
                    query,
                )
                if response is HANDLED:
                    return
                status, result, headers = (
                    response if response is not None else self.api(path)
                )
                self.send_json(result, status, headers, head_only)
            elif self.command in ("GET", "HEAD"):
                self.static_file(path, head_only)
            else:
                raise ApiError(405, "지원하지 않는 요청입니다.")
        except ApiError as error:
            self.send_json(
                {"error": error.message}, error.status, error.headers, head_only
            )
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            logging.getLogger("tpc").exception(
                "Request failed: %s", self.path.split("?")[0]
            )
            self.send_json(
                {"error": "처리 중 문제가 발생했습니다. 잠시 후 다시 시도해주세요."},
                500,
                head_only=head_only,
            )

    def check_origin(self):
        origin = self.headers.get("Origin", "")
        if origin not in self.app.origins:
            raise ApiError(403, "같은 사이트에서 다시 요청해주세요.")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site and fetch_site not in ("same-origin", "none"):
            raise ApiError(403, "같은 사이트에서 다시 요청해주세요.")

    def body(self, *, strict=False):
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(400, "지원하지 않는 요청 형식입니다.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(400, "요청 크기를 확인해주세요.")
        if length < 0 or length > MAX_BODY_BYTES:
            raise ApiError(413, "입력 내용이 너무 깁니다.")
        if (
            self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            != "application/json"
        ):
            raise ApiError(415, "JSON 형식으로 요청해주세요.")
        try:
            if strict and length:
                from .json_input import strict_loads
                data = strict_loads(self.rfile.read(length).decode("utf-8"))
            else:
                data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "입력 형식을 확인해주세요.")
        if not isinstance(data, dict):
            raise ApiError(400, "입력 형식을 확인해주세요.")
        return data

    def require_user(self, admin=False):
        if not self.user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        if admin and self.user["role"] != "admin":
            raise ApiError(403, "운영자만 이용할 수 있습니다.")
        return self.user

    def api(self, path):
        method = "GET" if self.command == "HEAD" else self.command
        empty_headers = {}
        if method == "GET" and path == "/api/health":
            return 200, {"ok": True}, empty_headers
        if method == "GET" and path == "/api/session":
            return 200, {"user": self.user}, empty_headers
        if method == "GET" and path == "/api/catalog":
            return 200, self.app.catalog.public_catalog(self.user), empty_headers
        if method == "GET" and path == "/api/practice/questions":
            questions = []
            for course in self.app.catalog.public_catalog(self.user)["courses"]:
                if course["status"] != "published":
                    continue
                for meta in course["lessons"]:
                    if meta["free"]:
                        lesson = self.app.catalog.find_lesson(course["id"], meta["id"])
                        if lesson.get("quiz"):
                            questions.append(
                                {
                                    "courseId": course["id"],
                                    "lessonId": meta["id"],
                                    "title": lesson["title"],
                                    "quiz": lesson["quiz"],
                                }
                            )
            return 200, {"questions": questions}, {}
        if method == "GET" and path == "/api/solver/status":
            return (
                200,
                {
                    "available": self.app.solver.available(),
                    "message": (
                        "솔버 연결 준비 중"
                        if not self.app.solver.available()
                        else "솔버 연결됨"
                    ),
                },
                empty_headers,
            )
        if method == "POST" and path in ("/api/auth/register", "/api/auth/login"):
            return self.authenticate(path, self.body())
        if method == "POST" and path == "/api/auth/logout":
            self.app.delete_session(self.headers.get("Cookie"))
            return 200, {"user": None}, {"Set-Cookie": self.app.session_cookie("", 0)}
        match = re.fullmatch(r"/api/courses/([a-z0-9-]+)/lessons/([a-z0-9-]+)", path)
        if method == "GET" and match:
            course_id, lesson_id = match.groups()
            lesson = self.app.catalog.find_lesson(course_id, lesson_id)
            if not lesson:
                raise ApiError(404, "강의를 찾을 수 없습니다.")
            published_course = self.app.catalog.find_course(course_id)
            if not lesson["free"] or published_course["status"] == "archived":
                user = self.require_user()
                with self.app.db.connect() as connection:
                    course = connection.execute(
                        "SELECT * FROM courses WHERE id=?", (course_id,)
                    ).fetchone()
                    if not self.app.enrollment(
                        connection, user["id"], course_id
                    ) and not can_manage_course(connection, user, course):
                        raise ApiError(
                            403, denial(for_member(connection, user["id"], course_id))
                        )
            if self.user:
                with self.app.db.connect() as connection:
                    watch = connection.execute(
                        "SELECT w.seconds,s.asset_id,s.user_id AS source_user_id FROM lesson_watch w LEFT JOIN lesson_watch_sources s ON s.user_id=w.user_id AND s.course_id=w.course_id AND s.lesson_id=w.lesson_id WHERE w.user_id=? AND w.course_id=? AND w.lesson_id=?",
                        (self.user["id"], course_id, lesson_id),
                    ).fetchone()
                    lesson["resumeSeconds"] = watch[0] if watch else 0
                    if (
                        watch
                        and lesson.get("videoAssetId")
                        and watch["asset_id"] != lesson["videoAssetId"]
                    ):
                        lesson["resumeSeconds"] = 0
                        lesson["resumeResetReason"] = (
                            "video_changed"
                            if watch["source_user_id"]
                            else "unverified_source"
                        )
            from .platform.resources import learner_resources

            with self.app.db.connect() as connection:
                connection.execute("BEGIN")
                lesson["resources"] = learner_resources(
                    connection, course_id, lesson.get("resources", [])
                )
                lesson["learning"] = self.app.platform.lesson_learning.attach(
                    connection, self.user, course_id, lesson_id, lesson
                )
                if lesson.get("videoAssetId"):
                    watch = self.app.platform.watch_progress.attach(
                        connection, self.user, course_id, lesson_id, lesson
                    )
                    lesson["captions"] = self.app.platform.captions.learner(
                        connection, self.user, course_id, lesson_id, lesson
                    )["captions"]
                    lesson["watchProgress"] = watch
                    lesson["videoDurationSeconds"] = watch["durationSeconds"]
                    lesson["videoSample"] = watch["isSample"]
                    lesson["resumeSeconds"] = watch["seconds"]
                    if watch["resetReason"]:
                        lesson["resumeResetReason"] = watch["resetReason"]
                    else:
                        lesson.pop("resumeResetReason", None)
            return 200, {"lesson": lesson}, empty_headers
        if method == "GET" and path == "/api/me":
            user = self.require_user()
            return 200, self.app.member_data(user["id"]), empty_headers
        if method == "POST" and path == "/api/enrollments":
            user = self.require_user()
            data = self.body()
            course_id = text_field(data, "courseId", maximum=100)
            with self.app.db.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                from .platform.pricing import select_price
                from .platform.policies import (
                    conditions_bundle,
                    explicit_selection,
                    record_consents,
                )

                course = connection.execute(
                    "SELECT * FROM courses WHERE id=?", (course_id,)
                ).fetchone()
                if not course:
                    raise ApiError(404, "강의를 찾을 수 없습니다.")
                quote = select_price(
                    connection, course, self.app.platform.commerce.gateway.mode
                )
                if not quote["ready"]:
                    raise ApiError(
                        409,
                        "현재 신규 수강을 받지 않는 상품입니다. 제공 조건을 확인해주세요.",
                    )
                if quote["amount"] != 0:
                    raise ApiError(403, "이 코스는 수강 신청 승인이 필요합니다.")
                bundle = None
                if not course["pilot"]:
                    bundle = conditions_bundle(
                        connection,
                        "checkout",
                        self.app.platform.commerce.gateway.mode,
                        course_id,
                    )
                    explicit_selection(bundle, data)
                enrollment = self.app.enroll(connection, user["id"], course_id)
                if enrollment is None:
                    raise ApiError(
                        403,
                        denial(for_member(connection, user["id"], course_id)),
                    )
                if bundle:
                    # Explicit free-registration consent is an account record;
                    # no payment order or financial approval is fabricated.
                    record_consents(connection, user["id"], bundle)
            return 200, {"enrollment": enrollment}, empty_headers
        if method == "PUT" and path == "/api/progress":
            return self.save_progress(self.body())
        if path == "/api/study-notes":
            user = self.require_user()
            if method == "GET":
                with self.app.db.connect() as connection:
                    notes = [
                        serialize(row)
                        for row in connection.execute(
                            "SELECT * FROM study_notes WHERE user_id=? ORDER BY updated_at DESC,id DESC",
                            (user["id"],),
                        ).fetchall()
                    ]
                return 200, {"notes": notes}, {}
            if method == "POST":
                return self.save_study_note(self.body())
        note_match = re.fullmatch(r"/api/study-notes/([a-z0-9_]+)", path)
        if note_match and method in ("PUT", "DELETE"):
            return self.save_study_note(
                self.body() if method == "PUT" else None,
                note_match.group(1),
                deleting=method == "DELETE",
            )
        if method == "POST" and path == "/api/practice/results":
            return self.save_practice(self.body())
        if method == "POST" and path == "/api/orders":
            return self.create_order(self.body())
        if method == "POST" and path == "/api/bookings":
            return self.create_booking(self.body())
        if method == "POST" and path == "/api/reviews":
            return self.create_review(self.body())
        match = re.fullmatch(r"/api/(bookings|reviews)/([a-z0-9_]+)", path)
        if method == "PATCH" and match:
            return self.cancel_member_item(*match.groups(), data=self.body())
        if method == "GET" and path == "/api/admin":
            return self.admin_data()
        match = re.fullmatch(r"/api/admin/(bookings|reviews|orders)/([a-z0-9_]+)", path)
        if method == "PATCH" and match:
            return self.update_admin_item(*match.groups(), data=self.body())
        raise ApiError(404, "요청한 기능을 찾을 수 없습니다.")

    def authenticate(self, path, data):
        ip = self.client_address[0]
        registering = path.endswith("register")
        self.app.limiter.check(("auth-ip", ip), 40, 600)
        email = email_field(data)
        password = password_field(data, registering)
        self.app.limiter.check(("auth-email", ip, email), 10, 600)
        if registering:
            self.app.limiter.check(("register", ip), 10, 3600)
            name = text_field(data, "name", minimum=2, maximum=50)
            user_id = new_id("usr")
            encoded = hash_password(password)
            try:
                with self.app.db.connect() as connection:
                    connection.execute(
                        "INSERT INTO users(id,name,email,password_hash,created_at) VALUES(?,?,?,?,?)",
                        (user_id, name, email, encoded, now_iso()),
                    )
            except sqlite3.IntegrityError:
                raise ApiError(409, "이미 가입된 이메일입니다. 로그인해주세요.")
            user = {"id": user_id, "name": name, "email": email, "role": "student"}
        else:
            with self.app.db.connect() as connection:
                row = connection.execute(
                    "SELECT * FROM users WHERE email=?", (email,)
                ).fetchone()
            valid = verify_password(
                password, row["password_hash"] if row else self.app.dummy_password
            )
            if not row or not valid:
                raise ApiError(401, "이메일 또는 비밀번호가 일치하지 않습니다.")
            if row["status"] != "active":
                raise ApiError(
                    403, "이용이 정지된 계정입니다. 운영자에게 문의해주세요."
                )
            user = public_user(row)
        cookie = self.app.create_session(user["id"], self.headers.get("Cookie", ""))
        return 201 if registering else 200, {"user": user}, {"Set-Cookie": cookie}

    def save_progress(self, data):
        return (
            200,
            self.app.platform.lesson_learning.progress(self.require_user(), data),
            {},
        )

    def save_study_note(self, data, note_id=None, deleting=False):
        user = self.require_user()
        if not deleting:
            title = text_field(data, "title", minimum=2, maximum=100)
            game_format = choice_field(data, "format", ("캐시", "토너먼트"))
            position = choice_field(
                data, "position", ("UTG", "HJ", "CO", "BTN", "SB", "BB")
            )
            stack = data.get("stack")
            if (
                isinstance(stack, bool)
                or not isinstance(stack, (int, float))
                or not 0 < stack <= 20000
            ):
                raise ApiError(400, "유효 스택은 0보다 크고 20,000BB 이하여야 합니다.")
            selected = data.get("selected")
            if (
                not isinstance(selected, list)
                or len(selected) > 169
                or any(
                    not isinstance(hand, str) or hand not in HAND_CLASSES
                    for hand in selected
                )
            ):
                raise ApiError(400, "올바른 핸드 클래스를 선택해주세요.")
            if len(set(selected)) != len(selected):
                raise ApiError(400, "중복된 핸드 클래스가 있습니다.")
            notes = text_field(data, "notes", maximum=10000, optional=True)
        if not note_id and not deleting:
            # Consume the shared quota before opening the note's write transaction.
            self.app.limiter.check(("notes", user["id"]), 60, 3600)
        with self.app.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if note_id:
                row = connection.execute(
                    "SELECT * FROM study_notes WHERE id=? AND user_id=?",
                    (note_id, user["id"]),
                ).fetchone()
                if not row:
                    raise ApiError(404, "스터디 노트를 찾을 수 없습니다.")
            if deleting:
                connection.execute(
                    "DELETE FROM study_notes WHERE id=? AND user_id=?",
                    (note_id, user["id"]),
                )
                return 200, {"deleted": True}, {}
            timestamp = now_iso()
            values = (
                title,
                game_format,
                position,
                stack,
                json.dumps(selected),
                notes,
                timestamp,
            )
            if note_id:
                connection.execute(
                    "UPDATE study_notes SET title=?,format=?,position=?,stack=?,selected=?,notes=?,updated_at=? WHERE id=? AND user_id=?",
                    values + (note_id, user["id"]),
                )
            else:
                note_id = new_id("note")
                connection.execute(
                    "INSERT INTO study_notes(title,format,position,stack,selected,notes,updated_at,id,user_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    values + (note_id, user["id"], timestamp),
                )
            row = connection.execute(
                "SELECT * FROM study_notes WHERE id=?", (note_id,)
            ).fetchone()
        return 200, {"note": serialize(row)}, {}

    def save_practice(self, data):
        user = self.require_user()
        self.app.limiter.check(("practice", user["id"]), 60, 3600)
        answers = data.get("answers")
        if not isinstance(answers, list) or not 1 <= len(answers) <= 30:
            raise ApiError(400, "완료한 퀴즈 답변을 확인해주세요.")
        validated, seen, correct = [], set(), 0
        for answer in answers:
            if not isinstance(answer, dict):
                raise ApiError(400, "퀴즈 답변을 확인해주세요.")
            course_id = text_field(answer, "courseId", maximum=100)
            lesson_id = text_field(answer, "lessonId", maximum=100)
            lesson = self.app.catalog.find_lesson(course_id, lesson_id)
            index = answer.get("answer")
            course = self.app.catalog.find_course(course_id)
            if (
                not lesson
                or not lesson["free"]
                or not lesson.get("quiz")
                or course["status"] != "published"
            ):
                raise ApiError(400, "공개된 퀴즈만 연습 결과로 저장할 수 있습니다.")
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or not 0 <= index < len(lesson["quiz"]["options"])
            ):
                raise ApiError(400, "퀴즈 선택지를 확인해주세요.")
            if (course_id, lesson_id) in seen:
                raise ApiError(400, "같은 퀴즈를 중복으로 제출할 수 없습니다.")
            seen.add((course_id, lesson_id))
            validated.append(
                {"courseId": course_id, "lessonId": lesson_id, "answer": index}
            )
            correct += int(index == lesson["quiz"]["answer"])
        with self.app.db.connect() as connection:
            attempt_id = new_id("practice")
            connection.execute(
                "INSERT INTO practice_attempts(id,user_id,answers,total,correct,created_at) VALUES(?,?,?,?,?,?)",
                (
                    attempt_id,
                    user["id"],
                    json.dumps(validated),
                    len(validated),
                    correct,
                    now_iso(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM practice_attempts WHERE id=?", (attempt_id,)
            ).fetchone()
        return 201, {"attempt": serialize(row)}, {}

    def create_order(self, data):
        user = self.require_user()
        course_id = text_field(data, "courseId", maximum=100)
        course = self.app.catalog.find_course(course_id)
        if not course:
            raise ApiError(404, "강의를 찾을 수 없습니다.")
        if course["status"] != "published":
            raise ApiError(409, "현재 신규 수강을 받지 않는 강의입니다.")
        if not course["pilot"]:
            raise ApiError(409, "정식 강의는 결제 화면에서 수강권을 구매해주세요.")
        with self.app.db.connect() as connection:
            # Serialize same-user course requests before checking idempotency.
            connection.execute("BEGIN IMMEDIATE")
            from .platform.pricing import select_price

            current = connection.execute(
                "SELECT * FROM courses WHERE id=?", (course_id,)
            ).fetchone()
            quoted = select_price(connection, current, "test")
            if not current["pilot"] or not quoted["ready"]:
                raise ApiError(409, "현재 신규 파일럿 접수를 받지 않습니다.")
            if quoted["amount"] == 0:
                raise ApiError(400, "무료 코스는 바로 수강 신청할 수 있습니다.")
            course["price"] = quoted["amount"]
            if self.app.enrollment(connection, user["id"], course_id):
                raise ApiError(409, "이미 수강 중인 코스입니다.")
            row = connection.execute(
                "SELECT * FROM orders WHERE user_id=? AND course_id=? AND status='접수'",
                (user["id"], course_id),
            ).fetchone()
            if row:
                return 200, {"order": serialize(row)}, {}
            item_id, timestamp = new_id("ord"), now_iso()
            connection.execute(
                "INSERT INTO orders(id,user_id,course_id,amount,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (item_id, user["id"], course_id, course["price"], timestamp, timestamp),
            )
            row = connection.execute(
                "SELECT * FROM orders WHERE id=?", (item_id,)
            ).fetchone()
        return 201, {"order": serialize(row)}, {}

    def create_booking(self, data):
        user = self.require_user()
        if any(key in data for key in ("productId", "productVersion", "slotId", "slotVersion", "selection")):
            raise ApiError(409, "이 경로는 희망 일정 접수입니다. 상품·슬롯 확정 요청으로 사용할 수 없습니다.")
        self.app.limiter.check(("submission", user["id"]), 30, 3600)
        coach_id = text_field(data, "coachId", maximum=100)
        booking_type = choice_field(data, "type", ("1:1 코칭", "핸드 리뷰 코칭"))
        preferred_date = text_field(data, "preferredDate", maximum=10)
        try:
            requested_date = date.fromisoformat(preferred_date)
        except ValueError:
            raise ApiError(400, "희망 날짜를 확인해주세요.")
        today = datetime.now(SEOUL).date()
        if requested_date < today or requested_date > today + timedelta(days=365):
            raise ApiError(400, "희망 날짜는 오늘부터 1년 이내로 선택해주세요.")
        preferred_time = choice_field(data, "preferredTime", ("오전", "오후", "저녁"))
        goal = text_field(data, "goal", minimum=10, maximum=3000)
        item_id, timestamp = new_id("bkg"), now_iso()
        with self.app.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            profile, coach = require_inquiry(connection, coach_id)
            expected_version = data.get("coachVersion")
            if expected_version is not None and (
                type(expected_version) is not int
                or expected_version != coach["publicVersion"]
            ):
                raise ApiError(
                    409,
                    "확인한 코치 소개가 바뀌었습니다. 최신 공개본을 확인한 뒤 신청해주세요.",
                )
            connection.execute(
                "INSERT INTO bookings(id,user_id,coach_id,type,preferred_date,preferred_time,goal,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    item_id,
                    user["id"],
                    coach_id,
                    booking_type,
                    preferred_date,
                    preferred_time,
                    goal,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM bookings WHERE id=?", (item_id,)
            ).fetchone()
            record_booking_profile(connection, item_id, profile, coach)
            result = {**serialize(row), **booking_routing(connection, row)}
        return 201, {"booking": result}, {}

    def create_review(self, data):
        user = self.require_user()
        self.app.limiter.check(("submission", user["id"]), 30, 3600)
        title = text_field(data, "title", minimum=2, maximum=100)
        game_type = choice_field(
            data, "gameType", ("라이브 캐시", "토너먼트", "온라인 캐시")
        )
        position = choice_field(
            data, "position", ("UTG", "HJ", "CO", "BTN", "SB", "BB")
        )
        stack = data.get("stack")
        if isinstance(stack, bool):
            raise ApiError(400, "유효 스택을 BB 단위로 입력해주세요.")
        try:
            stack = float(stack)
        except (TypeError, ValueError, OverflowError):
            raise ApiError(400, "유효 스택을 BB 단위로 입력해주세요.")
        if not 0 < stack <= 20000:
            raise ApiError(400, "유효 스택은 0보다 크고 20,000BB 이하여야 합니다.")
        hero = parse_cards(text_field(data, "heroHand", maximum=30), (2,), "홀 카드")
        board = parse_cards(
            text_field(data, "board", maximum=60, optional=True), (0, 3, 4, 5), "보드"
        )
        if len(set(hero + board)) != len(hero + board):
            raise ApiError(400, "같은 카드가 두 번 입력되었습니다.")
        actions = text_field(data, "actions", minimum=10, maximum=10000)
        question = text_field(data, "question", minimum=5, maximum=3000)
        item_id, timestamp = new_id("rev"), now_iso()
        with self.app.db.connect() as connection:
            connection.execute(
                "INSERT INTO reviews(id,user_id,title,game_type,position,stack,hero_hand,board,actions,question,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item_id,
                    user["id"],
                    title,
                    game_type,
                    position,
                    stack,
                    " ".join(hero),
                    " ".join(board),
                    actions,
                    question,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM reviews WHERE id=?", (item_id,)
            ).fetchone()
        return 201, {"review": serialize(row)}, {}

    def cancel_member_item(self, table, item_id, data):
        user = self.require_user()
        if data.get("status") != "취소":
            raise ApiError(400, "신청 취소만 할 수 있습니다.")
        with self.app.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM {} WHERE id=? AND user_id=?".format(table),
                (item_id, user["id"]),
            ).fetchone()
            if not row:
                raise ApiError(404, "신청을 찾을 수 없습니다.")
            if table == "reviews":
                self.app.platform.hand_reviews.legacy_guard(connection, row)
            if row["status"] != "접수":
                raise ApiError(
                    409, "처리가 시작된 신청입니다. 운영자에게 문의해주세요."
                )
            connection.execute(
                "UPDATE {} SET status='취소',updated_at=? WHERE id=?".format(table),
                (now_iso(), item_id),
            )
            row = connection.execute(
                "SELECT * FROM {} WHERE id=?".format(table), (item_id,)
            ).fetchone()
        return 200, {"booking" if table == "bookings" else "review": serialize(row)}, {}

    def admin_data(self):
        self.require_user(admin=True)
        result = {}
        with self.app.db.connect() as connection:
            for table in ("bookings", "reviews", "orders"):
                result[table] = [
                    (
                        {**serialize(row), **booking_routing(connection, row)}
                        if table == "bookings"
                        else serialize(self.app.platform.hand_reviews.project_legacy(connection, row)) if table == "reviews" else serialize(row)
                    )
                    for row in connection.execute(
                        "SELECT t.*,u.name AS user_name,u.email AS user_email FROM {} t JOIN users u ON u.id=t.user_id ORDER BY t.created_at DESC,t.id DESC".format(
                            table
                        ),
                    ).fetchall()
                ]
            result["stats"] = {
                "users": connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role='student'"
                ).fetchone()[0],
                "bookings": len(result["bookings"]),
                "reviews": len(result["reviews"]),
                "orders": len(result["orders"]),
                "pendingBookings": sum(
                    item["status"] == "접수" for item in result["bookings"]
                ),
                "pendingReviews": sum(
                    item["status"] in ("접수", "검토 중") for item in result["reviews"]
                ),
                "pendingOrders": sum(
                    item["status"] == "접수" for item in result["orders"]
                ),
            }
        return 200, result, {}

    def update_admin_item(self, table, item_id, data):
        self.require_user(admin=True)
        statuses = {
            "bookings": ("접수", "확정", "완료", "취소"),
            "reviews": ("접수", "검토 중", "답변 완료", "취소"),
            "orders": ("승인", "취소"),
        }
        status = choice_field(data, "status", statuses[table])
        reply = (
            text_field(data, "reply", maximum=10000, optional=True)
            if table != "orders"
            else ""
        )
        if table == "reviews" and status == "답변 완료" and not reply:
            raise ApiError(400, "리뷰 답변을 입력한 뒤 완료해주세요.")
        if table == "bookings" and status == "확정" and not reply:
            raise ApiError(400, "확정 일정과 안내를 답변에 입력해주세요.")
        with self.app.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM {} WHERE id=?".format(table), (item_id,)
            ).fetchone()
            if not row:
                raise ApiError(404, "신청을 찾을 수 없습니다.")
            if table == "reviews":
                self.app.platform.hand_reviews.legacy_guard(connection, row)
            if (
                table == "bookings"
                and status in ("확정", "완료")
                and booking_routing(connection, row)["needsHandoff"]
            ):
                raise ApiError(
                    409,
                    "담당 코치가 비활성 상태입니다. 활성 코치에게 접수를 인계한 뒤 진행해주세요.",
                )
            # A form loaded before cancellation must not silently reopen it.
            # Check inside the write transaction so a concurrent cancellation wins.
            if table != "orders" and row["status"] == "취소" and status != "취소":
                raise ApiError(
                    409,
                    "취소된 신청은 다시 진행할 수 없습니다. 새 신청을 접수해주세요.",
                )
            if table == "orders":
                # 승인/취소 are terminal. Repeating the same result is idempotent;
                # changing an approved order requires a future explicit refund /
                # enrollment-revocation workflow, not this status-update endpoint.
                if row["status"] != "접수":
                    if row["status"] == status:
                        return 200, {"order": serialize(row)}, {}
                    raise ApiError(409, "이미 처리된 수강 신청입니다.")
                if status == "승인":
                    self.app.enroll(
                        connection,
                        row["user_id"],
                        row["course_id"],
                        source="pilot",
                        source_id=item_id,
                    )
                connection.execute(
                    "UPDATE orders SET status=?,updated_at=? WHERE id=?",
                    (status, now_iso(), item_id),
                )
            else:
                connection.execute(
                    "UPDATE {} SET status=?,reply=?,updated_at=? WHERE id=?".format(
                        table
                    ),
                    (status, reply, now_iso(), item_id),
                )
            row = connection.execute(
                "SELECT * FROM {} WHERE id=?".format(table), (item_id,)
            ).fetchone()
        return (
            200,
            {
                {"bookings": "booking", "reviews": "review", "orders": "order"}[
                    table
                ]: serialize(row)
            },
            {},
        )

    def security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )

    def send_json(self, payload, status=200, extra_headers=None, head_only=False):
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.security_headers()
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if not head_only:
            self.wfile.write(encoded)

    def static_file(self, path, head_only=False):
        if (
            "\x00" in path
            or "\\" in path
            or any(part.startswith(".") for part in path.split("/"))
        ):
            raise ApiError(404, "페이지를 찾을 수 없습니다.")
        if path in ("/", "/index.html"):
            relative = "index.html"
        elif path in ("/styles.css", "/script.js", "/favicon.svg", "/robots.txt"):
            relative = path[1:]
        elif path in ("/proposal", "/proposal/"):
            relative = "proposal/index.html"
        elif path in (
            "/proposal/index.html",
            "/proposal/styles.css",
            "/proposal/script.js",
        ):
            relative = path[1:]
        elif path.startswith("/src/") and Path(path).suffix in (".js", ".css"):
            relative = path[1:]
        elif path.startswith("/assets/") and Path(path).suffix.lower() in (
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
            ".svg",
            ".ico",
            ".woff",
            ".woff2",
            ".mp4",
            ".webm",
            ".pdf",
        ):
            relative = path[1:]
        else:
            raise ApiError(404, "페이지를 찾을 수 없습니다.")
        target = (ROOT / relative).resolve()
        try:
            target.relative_to(ROOT)
        except ValueError:
            raise ApiError(404, "페이지를 찾을 수 없습니다.")
        # Resolve symlinks, then require their final target to stay in that public directory.
        if relative.startswith(("assets/", "src/", "proposal/")):
            allowed_root = ROOT / relative.split("/")[0]
            try:
                target.relative_to(allowed_root)
            except ValueError:
                raise ApiError(404, "페이지를 찾을 수 없습니다.")
        if not target.is_file():
            raise ApiError(404, "페이지를 찾을 수 없습니다.")
        content = target.read_bytes()
        content_type = (
            mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        )
        if content_type.startswith("text/") or content_type in (
            "application/javascript",
            "image/svg+xml",
        ):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.security_headers()
        self.end_headers()
        if not head_only:
            self.wfile.write(content)


class TPCServer(ThreadingHTTPServer):
    # Browsers can open many asset/API connections at once.
    request_queue_size = 64
    daemon_threads = True
    allow_reuse_address = True

    def serve_forever(self, poll_interval=0.5):
        self.application.platform.identity_recovery.start()
        try:
            super().serve_forever(poll_interval)
        finally:
            self.application.platform.identity_recovery.close()

    def server_close(self):
        application = getattr(self, "application", None)
        if application is not None:
            application.platform.reconciliation_runtime.stop()
            application.platform.identity_recovery.close()
        super().server_close()


def create_server(
    host="127.0.0.1", port=8000, db_path=None, origin=None, solver=None, gateway=None,
    *, identity_keyring=None
):
    server = TPCServer((host, port), TPCHandler)
    actual_port = server.server_address[1]
    if origin:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            server.server_close()
            raise ValueError("TPC_ORIGIN must be an http(s) origin without a path")
        origins = [origin.rstrip("/")]
    else:
        # DNS rebinding protection: do not accept arbitrary Host names.
        origins = [
            "http://127.0.0.1:{}".format(actual_port),
            "http://localhost:{}".format(actual_port),
        ]
        if host not in ("127.0.0.1", "localhost", "0.0.0.0", "::"):
            origins.append("http://{}:{}".format(host, actual_port))
    try:
        server.application = Application(
            db_path or ROOT / "data" / "tpc.sqlite3", origins, solver, gateway, identity_keyring=identity_keyring
        )
    except BaseException:
        server.server_close()
        raise
    server.application.platform.reconciliation_runtime.start(os.environ.get("TPC_RECONCILIATION_WORKER") == "1")
    return server
