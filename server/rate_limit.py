"""Durable sliding-window quotas shared by every process using the same DB."""

import hashlib
import hmac
import json
import math
import time

from .core import ApiError


class RateLimiter:
    def __init__(self, db, clock=None):
        self.db = db
        self.clock = clock or time.time
        with db.connect() as connection:
            self.key_secret = connection.execute(
                "SELECT key_secret FROM rate_limit_config WHERE id=1"
            ).fetchone()[0]

    def check(self, key, limit, period, *, busy_timeout_ms=None):
        if type(limit) is not int or limit < 1 or period <= 0:
            raise ValueError("A positive quota and window are required")
        encoded = json.dumps([key, period], ensure_ascii=False, separators=(",", ":"))
        digest = hmac.new(self.key_secret, encoded.encode(), hashlib.sha256).hexdigest()
        retry_after = None
        with self.db.connect() as connection:
            if busy_timeout_ms is not None:
                if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 5000:
                    raise ValueError("A bounded writer timeout is required")
                connection.execute("PRAGMA busy_timeout=" + str(busy_timeout_ms))
            # Serialize the prune/count/consume operation across threads and processes.
            # Read the wall clock after acquiring the write lock, not before waiting.
            connection.execute("BEGIN IMMEDIATE")
            current = self.clock()
            connection.execute(
                "DELETE FROM rate_limit_events WHERE expires_at<=?", (current,)
            )
            used, earliest = connection.execute(
                "SELECT COUNT(*),MIN(expires_at) FROM rate_limit_events WHERE key_hash=?",
                (digest,),
            ).fetchone()
            if used >= limit:
                retry_after = max(1, math.ceil(earliest - current))
            else:
                connection.execute(
                    "INSERT INTO rate_limit_events(key_hash,expires_at) VALUES(?,?)",
                    (digest, current + period),
                )
        if retry_after is not None:
            raise ApiError(
                429,
                "요청이 너무 많습니다. 잠시 후 다시 시도해주세요.",
                headers={"Retry-After": str(retry_after)},
            )

    def check_in(self, connection, key, limit, period, *, current):
        """Consume issuance quota in a caller-owned business transaction.

        Public invalid-request throttles use check() and its separate transaction.
        """
        if not connection.in_transaction or type(limit) is not int or limit < 1 or period <= 0:
            raise ValueError("An owned transaction and positive quota are required")
        encoded = json.dumps([key, period], ensure_ascii=False, separators=(",", ":"))
        digest = hmac.new(self.key_secret, encoded.encode(), hashlib.sha256).hexdigest()
        connection.execute("DELETE FROM rate_limit_events WHERE expires_at<=?", (current,))
        used, earliest = connection.execute(
            "SELECT COUNT(*),MIN(expires_at) FROM rate_limit_events WHERE key_hash=?", (digest,)
        ).fetchone()
        if used >= limit:
            raise ApiError(429, "요청이 너무 많습니다. 잠시 후 다시 시도해주세요.",
                           headers={"Retry-After": str(max(1, math.ceil(earliest-current)))})
        connection.execute("INSERT INTO rate_limit_events(key_hash,expires_at) VALUES(?,?)",
                           (digest, current+period))
