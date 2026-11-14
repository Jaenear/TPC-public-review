#!/usr/bin/env python3
"""Run TPC locally, or create a private operator account with an interactive password."""
import argparse
import getpass
import logging
import os
import sqlite3
import json
from pathlib import Path

from server.app import (
    ROOT,
    Application,
    create_server,
    email_field,
    hash_password,
    new_id,
    now_iso,
)
from server.database import Database
from server.migrations import LATEST_VERSION


def main():
    parser = argparse.ArgumentParser(description="TPC learning platform")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("serve", "create-admin", "install-sample", "install-sample-caption", "payments-status", "payments-run", "payments-watch"),
        default="serve",
    )
    parser.add_argument("--host", default=os.environ.get("TPC_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("TPC_PORT", "8000"))
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("TPC_DB_PATH", str(ROOT / "data" / "tpc.sqlite3")),
    )
    parser.add_argument("--email", help="Operator email for create-admin")
    parser.add_argument("--name", default="TPC 운영자", help="Operator display name")
    parser.add_argument(
        "--file", help="Owned MP4 for explicit local sample installation"
    )
    parser.add_argument("--course", default="preflop-foundations")
    parser.add_argument("--lesson", default="position")
    parser.add_argument("--expected-version", type=int)
    parser.add_argument("--expected-course-version", type=int)
    parser.add_argument("--expected-media-sha", help="Exact owned sample video SHA-256")
    parser.add_argument("--batch-size", type=int, default=20, help="Payment query batch size1–20")
    parser.add_argument("--interval", type=int, default=2, help="Payment query watch interval1–60 seconds")
    parser.add_argument("--allow-live-queries", action="store_true", help="Explicitly allow queries in the approved live environment")
    parser.add_argument(
        "--reason", default="사용자 요청: 자체 제작 프리플랍 학습용 샘플 연결"
    )
    args = parser.parse_args()
    if args.command.startswith("payments-"):
        from server.payments_worker import status as payment_status, run as payment_run
        try:
            if args.command == "payments-status":
                print(json.dumps(payment_status(args.db), ensure_ascii=False))
            else:
                payment_run(args.db, args.batch_size, args.command == "payments-watch", args.interval, args.allow_live_queries, lambda value: print(json.dumps(value, ensure_ascii=False), flush=True))
        except (ValueError, sqlite3.Error):
            parser.error("현재 스키마·원래 상점/조회 자격·허용 환경과 배치 설정을 확인해주세요. DB는 자동 이관하지 않습니다.")
        return
    if args.command in ("install-sample", "install-sample-caption"):
        if (
            not args.file
            or (
                args.command == "install-sample"
                and (
                    args.expected_version is None
                    or args.expected_course_version is None
                )
            )
            or (
                args.command == "install-sample-caption" and not args.expected_media_sha
            )
        ):
            parser.error(
                "install-sample requires --file and expected lesson/course versions; install-sample-caption requires --file and --expected-media-sha"
            )
        if not 5 <= len(args.reason.strip()) <= 500:
            parser.error("Enter a 5–500 character installation reason")
        # This command never silently upgrades an unbacked-up customer DB.
        uri = Path(args.db).resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            if (
                connection.execute("PRAGMA user_version").fetchone()[0]
                != LATEST_VERSION
            ):
                parser.error(
                    f"Media requires schema15 or later and the current schema{LATEST_VERSION}; back up and upgrade explicitly before installing media"
                )
        from server.sample_lesson import install_sample

        app = Application(
            args.db, {os.environ.get("TPC_ORIGIN") or "http://127.0.0.1:8000"}
        )
        try:
            if args.command == "install-sample-caption":
                from server.sample_caption import install_sample_caption

                result = install_sample_caption(
                    app,
                    args.file,
                    args.course,
                    args.lesson,
                    args.expected_media_sha,
                    args.reason.strip(),
                )
            else:
                result = install_sample(
                    app,
                    args.file,
                    args.course,
                    args.lesson,
                    args.expected_version,
                    args.expected_course_version,
                    args.reason.strip(),
                )
        except Exception as error:
            parser.error(str(error))
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.command == "create-admin":
        if not args.email:
            parser.error("create-admin requires --email")
        try:
            email = email_field({"email": args.email})
        except Exception:
            parser.error("Enter a valid email address")
        if not 2 <= len(args.name.strip()) <= 50:
            parser.error("Operator name must be 2–50 characters")
        password = getpass.getpass("비밀번호 (8자 이상): ")
        confirmation = getpass.getpass("비밀번호 확인: ")
        if not 8 <= len(password) <= 128 or password != confirmation:
            parser.error("비밀번호는 8~128자이며 확인 값과 같아야 합니다.")
        database = Database(args.db)
        try:
            with database.connect() as connection:
                connection.execute(
                    "INSERT INTO users(id,name,email,password_hash,role,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        new_id("usr"),
                        args.name.strip(),
                        email,
                        hash_password(password),
                        "admin",
                        now_iso(),
                    ),
                )
        except sqlite3.IntegrityError:
            parser.error("이미 등록된 이메일입니다. 다른 운영자 이메일을 사용해주세요.")
        print(
            "운영자 계정을 생성했습니다. 사이트에서 로그인하면 운영 메뉴를 이용할 수 있습니다."
        )
        return
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    server = create_server(args.host, args.port, args.db, os.environ.get("TPC_ORIGIN"))
    display_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    print(
        "TPC ready at http://{}:{}".format(display_host, server.server_address[1]),
        flush=True,
    )
    print("Stop with Ctrl+C. Data: {}".format(server.application.db.path), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTPC stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
