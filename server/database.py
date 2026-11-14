"""SQLite persistence. Each request owns a short-lived connection."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
from .migrations import migrate, LATEST_VERSION


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'student' CHECK(role IN ('student', 'admin')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
CREATE TABLE IF NOT EXISTS enrollments (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, course_id)
);
CREATE TABLE IF NOT EXISTS progress (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id TEXT NOT NULL,
    lesson_id TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY(user_id, course_id, lesson_id),
    FOREIGN KEY(user_id, course_id) REFERENCES enrollments(user_id, course_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    coach_id TEXT NOT NULL,
    type TEXT NOT NULL,
    preferred_date TEXT NOT NULL,
    preferred_time TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '접수',
    reply TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bookings_owner ON bookings(user_id, created_at);
CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    game_type TEXT NOT NULL,
    position TEXT NOT NULL,
    stack REAL NOT NULL,
    hero_hand TEXT NOT NULL,
    board TEXT NOT NULL,
    actions TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '접수',
    reply TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_owner ON reviews(user_id, created_at);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT '접수',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_owner ON orders(user_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS orders_one_pending ON orders(user_id, course_id) WHERE status='접수';
CREATE TABLE IF NOT EXISTS study_notes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    format TEXT NOT NULL,
    position TEXT NOT NULL,
    stack REAL NOT NULL,
    selected TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS study_notes_owner ON study_notes(user_id, updated_at);
CREATE TABLE IF NOT EXISTS practice_attempts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    answers TEXT NOT NULL,
    total INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS practice_owner ON practice_attempts(user_id, created_at);

"""


class Database:
    def __init__(self, path):
        self.path = str(Path(path).expanduser().resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] > LATEST_VERSION:
                raise RuntimeError(
                    "Database schema is newer than this application. Upgrade the application; do not reset the database."
                )
            self.enable_wal(connection)
            connection.executescript(SCHEMA)
            migrate(connection)
        # Keep account records private from other OS users on the same machine.
        Path(self.path).chmod(0o600)

    @staticmethod
    def enable_wal(connection):
        # SQLite may return BUSY immediately while two processes first switch
        # a legacy database to WAL, even with the connection busy timeout set.
        deadline = time.monotonic() + 10
        while True:
            try:
                if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                    connection.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as error:
                if (
                    str(error) not in ("database is locked", "database is busy")
                    or time.monotonic() >= deadline
                ):
                    raise
                time.sleep(0.02)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
