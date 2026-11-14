"""Current enrollment/start guidance; explicit guide reading is not progress."""

import hashlib
import json
from ..access import for_member, denial
from ..core import ApiError, now_iso
from .common import audit
from .pricing import select_price
from .policies import conditions_bundle

GUIDE_VERSION = "TPC-START-1"


def active_member(c, user):
    if user is None:
        return None
    row = c.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    if not row or row["status"] != "active":
        raise ApiError(401, "현재 계정으로 다시 로그인해주세요.")
    return row


class LearningStartService:
    def __init__(self, app):
        self.app = app

    def read(self, user, cid):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            return {"start": self.snapshot(c, user, cid)}

    def snapshot(self, c, user, cid):
        user = active_member(c, user)
        course = c.execute("SELECT * FROM courses WHERE id=?", (cid,)).fetchone()
        history = c.execute(
            "SELECT id FROM enrollments WHERE user_id=? AND course_id=?",
            (user["id"] if user else "", cid),
        ).fetchone()
        if not course or not (
            course["status"] == "published"
            or (course["status"] == "archived" and history)
        ):
            raise ApiError(404, "현재 시작할 수 있는 강의를 찾을 수 없습니다.")
        access = for_member(c, user["id"] if user else "", cid)
        lessons = c.execute(
            "SELECT l.id,l.title,l.version,l.free FROM course_lessons l JOIN course_sections s ON s.id=l.section_id AND s.course_id=l.course_id WHERE l.course_id=? AND l.status='published' ORDER BY s.sort_order,s.id,l.sort_order,l.id",
            (cid,),
        ).fetchall()
        completed = {
            r[0]
            for r in c.execute(
                "SELECT lesson_id FROM progress WHERE user_id=? AND course_id=?",
                (user["id"] if user else "", cid),
            )
        }
        count = sum(row["id"] in completed for row in lessons)
        next_lesson = next(
            (r for r in lessons if r["id"] not in completed),
            lessons[0] if lessons else None,
        )

        def link(row):
            return (
                {
                    "id": row["id"],
                    "title": row["title"],
                    "href": f"#/learn/{cid}/{row['id']}",
                }
                if row
                else None
            )

        preview = (
            next((r for r in lessons if r["free"]), None)
            if course["status"] == "published"
            else None
        )
        ack = c.execute(
            "SELECT acknowledged_at FROM learning_start_acknowledgements WHERE user_id=? AND course_id=? AND guide_version=?",
            (user["id"] if user else "", cid, GUIDE_VERSION),
        ).fetchone()
        basis = (
            hashlib.sha256(
                json.dumps(
                    [
                        GUIDE_VERSION,
                        user["id"] if user else None,
                        cid,
                        course["version"],
                        course["status"],
                        access["version"],
                        access["status"],
                        [(r["id"], r["version"]) for r in lessons],
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if user
            else None
        )
        gateway = self.app.platform.commerce.gateway
        quote = select_price(c, course, gateway.mode)
        ready = bool(quote["ready"])
        if (
            ready
            and not (course["pilot"] and quote["amount"] == 0)
            and gateway.mode not in ("test", "live")
        ):
            ready = False
        elif ready and not (course["pilot"] and quote["amount"] == 0):
            ready = conditions_bundle(c, "checkout", gateway.mode, cid)["ready"]
        purchase_ready = bool(
            ready
            and lessons
            and (
                quote["amount"] == 0
                or (
                    gateway.ready
                    and (
                        gateway.mode != "live"
                        or (not course["pilot"] and self.app.secure_cookies)
                    )
                )
            )
        )
        order = (
            c.execute(
                "SELECT id,status,mode FROM purchase_orders WHERE user_id=? AND course_id=? AND status IN ('created','pending','confirming','paid','refunding') ORDER BY CASE WHEN status IN ('pending','confirming','refunding') THEN 0 ELSE 1 END,created_at DESC,rowid DESC LIMIT 1",
                (user["id"] if user else "", cid),
            ).fetchone()
            if user
            else None
        )
        inquiry = (
            c.execute(
                "SELECT id,status FROM orders WHERE user_id=? AND course_id=? AND status='접수' ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (user["id"], cid),
            ).fetchone()
            if user and course["pilot"]
            else None
        )

        def action(kind, label, href):
            return {"kind": kind, "label": label, "href": href}

        if access["canAccess"]:
            state = (
                "complete"
                if lessons and count == len(lessons)
                else "learning" if lessons else "content_wait"
            )
            message = (
                "모든 공개 차시를 마쳤습니다. 필요한 내용을 다시 복습하세요."
                if state == "complete"
                else (
                    "현재 수강권으로 학습할 수 있습니다."
                    if lessons
                    else "공개 차시를 준비하고 있습니다. 기존 수강권과 기록은 유지됩니다."
                )
            )
            primary = (
                action(
                    "learn",
                    (
                        "복습 시작하기"
                        if state == "complete"
                        else "이어서 학습하기" if count else "첫 차시 시작하기"
                    ),
                    link(next_lesson)["href"],
                )
                if next_lesson
                else action("history", "내 수강 기록 확인", "#/my")
            )
        elif access["status"] in ("revoked", "expired", "scheduled"):
            state, message = access["status"], denial(access)
            primary = action("history", "내 수강권·학습 기록 확인", "#/my")
        elif order and order["status"] != "created":
            state, message = (
                "order_pending",
                "기존 주문의 처리 상태를 먼저 확인해주세요. 확인되지 않은 결제를 수강 완료로 표시하지 않습니다.",
            )
            primary = action("orders", "기존 주문 결과 확인", "#/purchases")
        elif inquiry:
            state, message = (
                "inquiry_pending",
                "파일럿 수강 문의가 접수돼 있습니다. 운영자의 수강 승인 전이며 결제 완료를 뜻하지 않습니다.",
            )
            primary = action("history", "파일럿 수강 문의 확인", "#/my?tab=orders")
        elif not lessons or course["status"] != "published":
            state, message = (
                "content_wait",
                "현재 시작할 공개 차시를 준비하고 있습니다.",
            )
            primary = action("library", "다른 강의 둘러보기", "#/library")
        elif not user:
            state, message = (
                "sign_in",
                "로그인하면 본인의 수강권과 학습 기록에 연결해 시작할 수 있습니다.",
            )
            primary = action("login", "로그인하고 시작 준비", f"#/start/{cid}")
        elif purchase_ready and quote["amount"] == 0:
            state, message = (
                "free",
                "무료 수강을 등록하고 이 계정에 학습 기록을 남기세요.",
            )
            primary = action(
                "register" if course["pilot"] else "checkout",
                "무료 수강 등록" if course["pilot"] else "제공 조건 확인·무료 등록",
                f"#/checkout/{cid}",
            )
        elif purchase_ready:
            state, message = (
                "purchase",
                "제공 조건과 금액을 확인한 뒤 결제사의 승인 검증을 거쳐 수강권이 발급됩니다.",
            )
            primary = action("checkout", "수강 옵션·결제 확인", f"#/checkout/{cid}")
        else:
            state, message = (
                "preparing",
                "새 수강의 제공 조건과 결제 준비를 확인하고 있습니다.",
            )
            primary = action(
                "checkout", "수강 제공·준비 상태 확인", f"#/checkout/{cid}"
            )
        return {
            "course": {
                "id": cid,
                "title": course["title"],
                "subtitle": course["subtitle"],
                "status": course["status"],
                "version": course["version"],
                "pilot": bool(course["pilot"]),
            },
            "guideVersion": GUIDE_VERSION,
            "basisHash": basis,
            "state": state,
            "message": message,
            "signedIn": bool(user),
            "hasHistory": bool(history),
            "access": {
                k: access[k]
                for k in ("status", "canAccess", "expiresAt", "nextStartsAt")
            },
            "acknowledgement": {
                "confirmed": bool(ack),
                "acknowledgedAt": ack[0] if ack else None,
            },
            "canAcknowledge": bool(user and access["canAccess"]),
            "progress": {"completed": count, "total": len(lessons)},
            "preview": link(preview),
            "nextLesson": link(next_lesson) if access["canAccess"] else None,
            "primary": primary,
            "order": dict(order) if order else None,
            "inquiry": dict(inquiry) if inquiry else None,
            "purchase": {
                "ready": purchase_ready,
                "price": quote["amount"],
                "mode": gateway.mode,
                "configured": bool(gateway.ready),
            },
            "questionsHref": (
                f"#/qa/new?courseId={cid}"
                + (f"&lessonId={next_lesson['id']}" if next_lesson else "")
                if access["canAccess"]
                else None
            ),
        }

    def acknowledge(self, user, cid, data):
        if not user:
            raise ApiError(401, "로그인 후 학습 안내를 확인해주세요.")
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            start = self.snapshot(c, user, cid)
            if not start["canAcknowledge"]:
                raise ApiError(
                    403,
                    "현재 활성 수강권이 있어야 학습 안내 확인을 기록할 수 있습니다.",
                )
            if (
                data.get("guideVersion") != GUIDE_VERSION
                or data.get("basisHash") != start["basisHash"]
            ):
                raise ApiError(
                    409,
                    "수업 또는 수강권의 기준이 바뀌었습니다. 시작 안내를 다시 확인해주세요.",
                )
            inserted = c.execute(
                "INSERT OR IGNORE INTO learning_start_acknowledgements VALUES(?,?,?,?)",
                (user["id"], cid, GUIDE_VERSION, now_iso()),
            )
            if inserted.rowcount:
                audit(
                    c,
                    user,
                    "learning.start_acknowledged",
                    "course",
                    cid,
                    {"guideVersion": GUIDE_VERSION},
                )
            return {"start": self.snapshot(c, user, cid)}
