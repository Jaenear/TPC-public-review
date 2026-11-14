"""WSGI transport for the unchanged TPC application, with private on-host SQLite.

No listening socket, background task, framework or new package is required.
The host's WSGI server owns connection and request timeouts.
"""
from email.message import Message
from http import HTTPStatus
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.app import Application
from server.sample_lesson import install_sample
from deployment.serve_review import preflight, ReviewHandler

MAX_REQUEST_BYTES = 100 * 1024 * 1024


class RequestInput:
    """Never read beyond Content-Length or wait for EOF on a live WSGI input."""
    def __init__(self, stream, length):
        self.stream, self.remaining = stream, length

    def read(self, size=-1):
        size = self.remaining if size is None or size < 0 else min(size, self.remaining)
        if size == 0:
            return b""
        result = self.stream.read(size)
        self.remaining -= len(result)
        return result

    read1 = read


class HostConnection:
    """Compatibility only: socket-level timeouts belong to the WSGI host."""
    def __init__(self):
        self.timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def gettimeout(self):
        return self.timeout


class Handler(ReviewHandler):
    def __init__(self, app, environ, output, length):
        self.server = SimpleNamespace(application=app)
        self.command = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/").encode("latin-1").decode("utf-8")
        self.path = quote(path or "/", safe="/")
        query = environ.get("QUERY_STRING", "")
        if query:
            self.path += "?" + query
        self.request_version = environ.get("SERVER_PROTOCOL", "HTTP/1.1")
        self.client_address = (environ.get("REMOTE_ADDR", "unknown"), 0)
        self.connection = HostConnection()
        self.rfile = RequestInput(environ["wsgi.input"], length)
        self.wfile = output
        self.headers = Message()
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                self.headers[key[5:].replace("_", "-")] = value
        for key, name in (("CONTENT_LENGTH", "Content-Length"), ("CONTENT_TYPE", "Content-Type")):
            if key in environ:
                self.headers[name] = environ[key]
        self.status = 500
        self.response_headers = []

    def send_response(self, code, message=None):
        # WSGI headers are committed only after dispatch returns. A handled
        # exception can replace an incomplete response without appending JSON.
        self.wfile.seek(0)
        self.wfile.truncate()
        self.status = int(code)
        self.response_headers = []

    def send_header(self, name, value):
        self.response_headers.append((str(name), str(value)))

    def end_headers(self):
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")


class WSGIApplication:
    def __init__(self, app):
        self.tpc = app

    def __call__(self, environ, start_response):
        raw_length = environ.get("CONTENT_LENGTH", "")
        try:
            length = int(raw_length) if raw_length else 0
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise ValueError
        except (ValueError, TypeError):
            body = json.dumps({"error": "요청 크기를 확인해주세요."}, ensure_ascii=False).encode()
            start_response("413 Content Too Large", [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(body))), ("Cache-Control", "no-store")])
            return [b"" if environ.get("REQUEST_METHOD") == "HEAD" else body]
        output = tempfile.SpooledTemporaryFile(max_size=256 * 1024, mode="w+b")
        try:
            handler = Handler(self.tpc, environ, output, length)
            if handler.command == "OPTIONS":
                handler.do_OPTIONS()
            elif handler.command in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
                handler.dispatch(head_only=handler.command == "HEAD")
            else:
                handler.send_json({"error": "지원하지 않는 요청입니다."}, 405)
            status = f"{handler.status} {HTTPStatus(handler.status).phrase}"
            start_response(status, handler.response_headers)
            output.seek(0)
        except BaseException:
            output.close()
            raise

        def response():
            try:
                while True:
                    chunk = output.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                output.close()
        return response()


def make_application(origin, data_directory=None):
    parsed = urlsplit(origin)
    if (parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise ValueError("Set the exact public origin without a path, query or credentials.")
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
        raise ValueError("A remote WSGI application requires HTTPS.")
    origin = origin.rstrip("/")
    db, marker, sample = preflight(data_directory or ROOT / "review-data")
    fresh = not db.exists()
    app = Application(db, {origin})
    if app.platform.commerce.gateway.mode != "unconfigured":
        raise ValueError("Public review must not use merchant credentials.")
    if fresh:
        with app.db.connect() as connection:
            if connection.execute("SELECT count(*) FROM users").fetchone()[0] or connection.execute("SELECT count(*) FROM sessions").fetchone()[0]:
                raise ValueError("A fresh review must not contain accounts or sessions.")
        marker.write_text(json.dumps({"scope": "tpc-public-review-only", "schema": 33}) + "\n")
        marker.chmod(0o600)
    with app.db.connect() as connection:
        lesson = connection.execute("SELECT version FROM course_lessons WHERE course_id=? AND id=?", ("preflop-foundations", "position")).fetchone()[0]
        course = connection.execute("SELECT version FROM courses WHERE id=?", ("preflop-foundations",)).fetchone()[0]
    install_sample(app, sample, "preflop-foundations", "position", lesson, course, "무료 호스팅 외부 검토: 자체 제작 샘플 연결")
    return WSGIApplication(app)
