"""Real TPC auth/media/progress over the WSGI interface using temporary SQLite."""
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator

from deployment.wsgi import make_application, RequestInput, MAX_REQUEST_BYTES

ORIGIN = "https://tpc-wsgi-test.pythonanywhere.com"


class WSGITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "private-data"
        self.app = make_application(ORIGIN, self.directory)

    def tearDown(self):
        self.temp.cleanup()

    def request(self, method, path, body=None, cookie=None, origin=ORIGIN, headers=None, raw=None):
        environ = {}
        setup_testing_defaults(environ)
        environ.update(REQUEST_METHOD=method, PATH_INFO=path, QUERY_STRING="", HTTP_HOST="tpc-wsgi-test.pythonanywhere.com", REMOTE_ADDR="192.0.2.1", **{"wsgi.url_scheme": "https"})
        payload = raw if raw is not None else json.dumps(body).encode() if body is not None else b""
        environ["wsgi.input"] = io.BytesIO(payload)
        if payload or method not in ("GET", "HEAD"):
            environ.update(CONTENT_LENGTH=str(len(payload)), CONTENT_TYPE="application/json")
        if origin is not None:
            environ["HTTP_ORIGIN"] = origin
        if cookie:
            environ["HTTP_COOKIE"] = cookie
        for key, value in (headers or {}).items():
            environ["HTTP_" + key.upper().replace("-", "_")] = value
        result = {}
        def start_response(status, response_headers, exc_info=None):
            result.update(status=int(status.split()[0]), headers=dict(response_headers))
        stream = validator(self.app)(environ, start_response)
        try:
            content = b"".join(stream)
        finally:
            stream.close()
        data = json.loads(content) if content and result["headers"].get("Content-Type", "").startswith("application/json") else content
        return result["status"], data, result["headers"]

    def member(self, suffix="a"):
        status, result, headers = self.request("POST", "/api/auth/register", {"name": "WSGI 검수 " + suffix, "email": suffix + "@example.com", "password": "WSGI-review-2026"})
        self.assertEqual(status, 201, result)
        self.assertIn("Secure", headers["Set-Cookie"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        return headers["Set-Cookie"].split(";", 1)[0], result["user"]

    def test_public_page_private_paths_and_host_boundary(self):
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertEqual(self.request("GET", "/api/session")[1], {"user": None})
        for path in ("/.git/config", "/.env", "/review-data/review.sqlite3", "/server/app.py", "/deployment/wsgi.py", "/assets/../server/app.py"):
            self.assertEqual(self.request("GET", path)[0], 404, path)
        self.assertEqual(self.request("GET", "/api/session", headers={"Host": "attacker.example"})[0], 403)

    def test_fresh_private_database_and_reload_preserve_sample_identity(self):
        with self.app.tpc.db.connect() as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 33)
            self.assertEqual(c.execute("SELECT count(*) FROM users").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM sessions").fetchone()[0], 0)
        first = self.request("GET", "/api/courses/preflop-foundations/lessons/position")[1]["lesson"]
        self.app = make_application(ORIGIN, self.directory)
        second = self.request("GET", "/api/courses/preflop-foundations/lessons/position")[1]["lesson"]
        self.assertEqual((first["videoAssetId"], first["version"]), (second["videoAssetId"], second["version"]))
        with self.app.tpc.db.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM media_assets").fetchone()[0], 1)

    def test_login_origin_identity_and_logout_guards_are_preserved(self):
        cookie, user = self.member()
        self.assertEqual(self.request("POST", "/api/auth/logout", {}, cookie, origin=None)[0], 403)
        self.assertEqual(self.request("POST", "/api/auth/logout", {}, cookie, origin="https://attacker.example")[0], 403)
        self.assertEqual(self.request("GET", "/api/me", cookie=cookie, headers={"X-TPC-User": "usr_other"})[0], 401)
        self.assertEqual(self.request("GET", "/api/manage/users", cookie=cookie)[0], 403)
        self.assertEqual(self.request("POST", "/api/auth/logout", {}, cookie)[0], 200)
        self.assertIsNone(self.request("GET", "/api/session", cookie=cookie)[1]["user"])
        result = self.request("POST", "/api/auth/login", {"email": "a@example.com", "password": "WSGI-review-2026"})
        self.assertEqual(result[0], 200)
        self.assertEqual(result[1]["user"]["id"], user["id"])

    def test_video_range_and_head_do_not_change_bytes(self):
        lesson = self.request("GET", "/api/courses/preflop-foundations/lessons/position")[1]["lesson"]
        path = "/api/media/" + lesson["videoAssetId"]
        status, content, headers = self.request("GET", path, headers={"Range": "bytes=0-1023"})
        self.assertEqual(status, 206)
        self.assertEqual(len(content), 1024)
        self.assertTrue(headers["Content-Range"].startswith("bytes 0-1023/"))
        status, content, headers = self.request("HEAD", path)
        self.assertEqual((status, content), (200, b""))
        self.assertGreater(int(headers["Content-Length"]), 1000000)
        self.assertEqual(self.request("GET", path, headers={"Range": "bytes=99999999-"})[0], 416)

    def test_members_resume_after_application_reload_and_remain_isolated(self):
        a, user = self.member("a")
        b, _ = self.member("b")
        for cookie in (a, b):
            self.assertEqual(self.request("POST", "/api/enrollments", {"courseId": "preflop-foundations"}, cookie)[0], 200)
        path = "/api/courses/preflop-foundations/lessons/position"
        lesson = self.request("GET", path, cookie=a)[1]["lesson"]
        payload = {"courseId": "preflop-foundations", "lessonId": "position", "videoAssetId": lesson["videoAssetId"], "lessonVersion": lesson["version"], "seconds": 24, "watchVersion": 0, "requestKey": "wsgi-position-save-0001"}
        self.assertEqual(self.request("PUT", "/api/watch", payload, a, headers={"X-TPC-User": user["id"]})[0], 200)
        self.app = make_application(ORIGIN, self.directory)
        self.assertEqual(self.request("GET", path, cookie=a)[1]["lesson"]["resumeSeconds"], 24)
        self.assertEqual(self.request("GET", path, cookie=b)[1]["lesson"]["resumeSeconds"], 0)

    def test_question_community_and_private_visibility_over_wsgi(self):
        a, _ = self.member("a")
        b, _ = self.member("b")
        self.request("POST", "/api/enrollments", {"courseId": "preflop-foundations"}, a)
        result = self.request("POST", "/api/qa/questions", {"courseId": "preflop-foundations", "lessonId": "position", "title": "WSGI 강의 질문 검수", "body": "무료 호스팅에서 질문과 비공개 권한을 확인합니다.", "visibility": "private"}, a)
        self.assertEqual(result[0], 200, result[1])
        path = "/api/qa/questions/" + result[1]["question"]["id"]
        self.assertEqual(self.request("GET", path, cookie=b)[0], 403)
        self.request("POST", "/api/enrollments", {"courseId": "preflop-foundations"}, b)
        self.assertEqual(self.request("GET", path, cookie=b)[0], 404)
        result = self.request("POST", "/api/community/posts", {"boardId": "general", "title": "WSGI 게시글 저장 확인", "body": "외부 WSGI 호스팅의 커뮤니티 글 저장과 작성자 권한을 확인합니다."}, a)
        self.assertEqual(result[0], 200, result[1])
        post = result[1]["post"]
        result = self.request("PATCH", "/api/community/posts/" + post["id"], {"version": post["version"], "title": "다른 회원 수정 시도", "body": "작성자가 아닌 회원은 이 글을 수정할 수 없습니다."}, b)
        self.assertEqual(result[0], 403)

    def test_invalid_json_size_and_unsupported_methods(self):
        self.assertEqual(self.request("POST", "/api/auth/register", raw=b"{bad-json")[0], 400)
        self.assertEqual(self.request("POST", "/api/auth/register", raw=b"x" * 65537)[0], 413)
        self.assertEqual(self.request("OPTIONS", "/api/session")[0], 405)
        self.assertEqual(self.request("TRACE", "/api/session")[0], 405)

    def test_bounded_input_never_consumes_the_following_request(self):
        stream = io.BytesIO(b"1234next-request")
        bounded = RequestInput(stream, 4)
        self.assertEqual(bounded.read1(999), b"1234")
        self.assertEqual(bounded.read(), b"")
        self.assertEqual(stream.read(), b"next-request")


if __name__ == "__main__":
    unittest.main()
