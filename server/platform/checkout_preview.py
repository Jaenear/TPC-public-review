"""Point-in-time checkout eligibility. No gateway calls, consent or grants."""

from ..core import now_iso
from .common import has_access
from .order_products import purchase_snapshot
from .policies import conditions_bundle, public_bundle


def checkout_preview(app, c, user, order, payment):
    snapshot = purchase_snapshot(c, order)
    blockers = []

    def block(code, message):
        blockers.append({"code": code, "message": message})

    at = now_iso()
    current = None
    course = c.execute(
        "SELECT * FROM courses WHERE id=?", (order["course_id"],)
    ).fetchone()
    if order["status"] == "paid":
        block(
            "already_paid",
            "이미 승인된 주문입니다. 내 주문에서 현재 수강권을 확인해주세요.",
        )
    elif order["status"] == "abandoned":
        block(
            "order_closed",
            "승인 요청이 종료된 주문입니다. 최신 조건을 읽고 새 주문을 준비해주세요.",
        )
    elif order["status"] != "created" or order["payment_key"]:
        block(
            "payment_started",
            "새 인증을 시작하기 전에 내 주문에서 기존 처리 결과를 확인해주세요.",
        )
    if not snapshot["recorded"]:
        block(
            "unrecorded_product",
            "주문 당시 상품 기록이 없는 이전 주문입니다. 현재 조건으로 새 주문을 준비해주세요.",
        )
    elif snapshot["policy"]["paymentDeadline"] <= at:
        block(
            "expired",
            "주문 준비 시간이 지났습니다. 최신 조건을 확인하고 다시 동의해주세요.",
        )
    if not payment["configured"] or payment["mode"] != order["mode"]:
        block(
            "payment_mode_changed",
            "결제 연결 상태가 바뀌었습니다. 현재 조건을 새로 확인해주세요.",
        )
    if not course or course["status"] != "published":
        block(
            "course_unavailable",
            "현재 판매 중인 강의가 아닙니다. 기존 주문 기록은 보존됩니다.",
        )
    else:
        if payment["mode"] == "live" and (course["pilot"] or not app.secure_cookies):
            block(
                "live_unavailable",
                "파일럿 강의 또는 로컬 환경에서는 실제 결제를 받을 수 없습니다.",
            )
        if has_access(c, user, course["id"]):
            block(
                "already_enrolled",
                "이미 수강권이 있습니다. 현재 학습 화면을 확인해주세요.",
            )
        if payment["mode"] in ("test", "live"):
            bundle = conditions_bundle(c, "checkout", payment["mode"], course["id"], at)
            current = public_bundle(bundle)
            if not bundle["ready"]:
                block(
                    "conditions_unavailable",
                    "현재 가격·제공 조건을 확인 중입니다. 결제를 시작할 수 없습니다.",
                )
            elif snapshot["recorded"] and (
                bundle["fingerprint"] != snapshot["policy"]["fingerprint"]
                or bundle["course"]["product"] != snapshot["product"]
            ):
                block(
                    "conditions_changed",
                    "가격 또는 제공 조건이 바뀌었습니다. 최신 원문을 읽고 다시 동의해주세요.",
                )
    if c.execute(
        "SELECT 1 FROM purchase_orders WHERE user_id=? AND course_id=? AND id!=? AND status IN ('pending','confirming','refunding')",
        (user["id"], order["course_id"], order["id"]),
    ).fetchone():
        block(
            "other_payment_unconfirmed",
            "승인 결과를 확인해야 하는 다른 주문이 있습니다. 내 주문에서 먼저 확인해주세요.",
        )
    return {
        "asOf": at,
        "canStart": not blockers,
        "blockers": blockers,
        "purchaseSnapshot": snapshot,
        "policies": current,
        "payment": payment,
    }
