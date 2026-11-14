"""Local operator CLI for read-only status and explicit GET-based query work."""

import os
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from .database import Database
from .migrations import LATEST_VERSION
from .platform.commerce import CommerceService, TossGateway
from .platform.reconciliation import ReconciliationService
from .platform.reconciliation_ingress import configured_scope
from .platform.reconciliation_runtime import ReconciliationRuntime
from .rate_limit import RateLimiter


def status(path):
    """Does not construct Database/Application, migrate, seed or contact a PG."""
    uri = Path(path).expanduser().resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as c:
        c.execute("BEGIN")
        schema = c.execute("PRAGMA user_version").fetchone()[0]
        present = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reconciliation_jobs'").fetchone()
        counts = dict(c.execute("SELECT state,count(*) FROM reconciliation_jobs GROUP BY state")) if present else {}
    return {"schema": schema, "requiredSchema": LATEST_VERSION, "schemaReady": schema == LATEST_VERSION, "jobs": counts, "readOnly": True}


def context(path, allow_live=False, gateway=None):
    current = status(path)
    if not current["schemaReady"]:
        raise ValueError("schema{} is required; verify the backup and upgrade the application first".format(LATEST_VERSION))
    gateway = gateway or TossGateway()
    if configured_scope(gateway) is None:
        raise ValueError("Configure the original merchant and payment environment before running queries")
    origin = urlsplit(os.environ.get("TPC_ORIGIN", ""))
    secure = bool(origin.scheme == "https" and origin.hostname and origin.path in ("", "/") and not origin.query and not origin.fragment and not origin.username)
    if gateway.mode == "live" and (not allow_live or not secure):
        raise ValueError("Live queries require an explicit flag and the approved HTTPS origin")
    db = Database(path)  # Already checked exact schema. Never creates a missing DB.
    app = SimpleNamespace(db=db, secure_cookies=secure, limiter=RateLimiter(db))
    commerce = CommerceService(app, gateway)
    model = ReconciliationService(app)
    return ReconciliationRuntime(app, commerce, model)


def run(path, limit=20, watch=False, interval=2, allow_live=False, emit=None):
    if type(limit) is not int or not 1 <= limit <= 20 or type(interval) is not int or not 1 <= interval <= 60:
        raise ValueError("Use a batch of1–20 and interval of1–60 seconds")
    runtime = context(path, allow_live)
    try:
        while True:
            result = {"schema": LATEST_VERSION, "results": runtime.run_batch(limit), "configuration": runtime.describe()}
            if emit:
                emit(result)
            if not watch:
                return result
            runtime.stop_event.wait(interval)
    except KeyboardInterrupt:
        return {"stopped": True}
    finally:
        runtime.stop()
