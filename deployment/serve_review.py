#!/usr/bin/env python3
"""Run the isolated, schema-33 public review; never open a customer database."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.app import TPCHandler, create_server
from server.migrations import LATEST_VERSION
from server.sample_lesson import install_sample

SCHEMA = 33
SAMPLE_SHA256 = "1d2cf29f94ecab4cdf0f1af93f28515b6da7f5c2e2a4f82f397d46ef9535dc39"


def preflight(directory):
    """Reject foreign databases and schema upgrades before constructing the app."""
    if LATEST_VERSION != SCHEMA:
        raise ValueError("This review launcher requires the frozen schema-33 release.")
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("Review data directory must not be a symlink.")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    db = directory / "review.sqlite3"
    marker = directory / ".tpc-review.json"
    if db.is_symlink() or marker.is_symlink():
        raise ValueError("Review database and ownership marker must be regular files.")
    if db.exists():
        if not marker.is_file() or json.loads(marker.read_text()) != {"scope": "tpc-public-review-only", "schema": SCHEMA}:
            raise ValueError("Existing database has no matching public-review ownership marker.")
        with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA:
                raise ValueError("Existing database needs a separately backed-up upgrade; startup stopped.")
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Review database integrity check failed; startup stopped.")
    for key in os.environ:
        if key.startswith("TPC_TOSS_") and os.environ[key]:
            raise ValueError("Merchant credentials are not allowed in the public-review process.")
    if os.environ.get("TPC_ALLOW_LIVE_PAYMENTS") == "1" or os.environ.get("TPC_RECONCILIATION_WORKER") == "1":
        raise ValueError("Live payments and reconciliation workers are disabled in public review.")
    sample = ROOT / "review-media/preflop-sample.mp4"
    if not sample.is_file() or hashlib.sha256(sample.read_bytes()).hexdigest() != SAMPLE_SHA256:
        raise ValueError("Owned review sample failed SHA-256 verification.")
    return db, marker, sample


class ReviewHandler(TPCHandler):
    def end_headers(self):
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
        super().end_headers()


def start(origin, port=8025, host="127.0.0.1", directory=None):
    parsed = urlsplit(origin)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")):
        raise ValueError("Public review requires HTTPS; HTTP is only allowed on localhost.")
    db, marker, sample = preflight(directory or ROOT / "review-data")
    fresh = not db.exists()
    server = create_server(host=host, port=port, db_path=db, origin=origin)
    try:
        app = server.application
        if app.platform.commerce.gateway.mode != "unconfigured":
            raise ValueError("The public-review payment gateway must remain unconfigured.")
        if fresh:
            with app.db.connect() as conn:
                if conn.execute("SELECT count(*) FROM users").fetchone()[0] or conn.execute("SELECT count(*) FROM sessions").fetchone()[0]:
                    raise ValueError("A fresh review must not contain member accounts or sessions.")
            marker.write_text(json.dumps({"scope": "tpc-public-review-only", "schema": SCHEMA}) + "\n")
            marker.chmod(0o600)
        with app.db.connect() as conn:
            lesson_version = conn.execute("SELECT version FROM course_lessons WHERE course_id=? AND id=?", ("preflop-foundations", "position")).fetchone()[0]
            course_version = conn.execute("SELECT version FROM courses WHERE id=?", ("preflop-foundations",)).fetchone()[0]
        install_sample(app, sample, "preflop-foundations", "position", lesson_version, course_version, "외부 검토 전용: 자체 제작 무료 샘플 설치")
        server.RequestHandlerClass = ReviewHandler
        return server
    except BaseException:
        server.server_close()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True, help="Exact external HTTPS origin")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8025")))
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    args = parser.parse_args()
    server = start(args.origin, args.port, args.host)
    print(f"TPC isolated review ready: {args.origin}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
