"""Approved price versions, sale controls, policy anchors and order receipts.

No commercial price is seeded. Pilot amounts retain their example classification.
A later effective price supersedes earlier prices; withdrawal/expiry never falls
back. Old order prices and already-started financial results remain unchanged.
"""

import hashlib
import json
import sqlite3
from ..core import ApiError, now_iso, new_id, text_field, choice_field
from ..access import canonical_time
from .common import can_manage_course, version, page_args, audit, notify
from .contracts import current_actor, qualified


def encode(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(x):
    return hashlib.sha256(encode(x).encode()).hexdigest()


def mode_value(query):
    value = query.get("mode", ["test"])[0]
    if value not in ("test", "live"):
        raise ApiError(400, "가격 환경을 확인해주세요.")
    return value


def managed(c, user, cid, admin=False):
    user = current_actor(c, user, admin)
    course = c.execute("SELECT * FROM courses WHERE id=?", (cid,)).fetchone()
    if not course or not can_manage_course(c, user, course):
        raise ApiError(404, "조회할 수 있는 담당 강의를 찾을 수 없습니다.")
    return user, course


def course_snapshot(course):
    return {
        k: course[k]
        for k in (
            "id",
            "title",
            "subtitle",
            "description",
            "category",
            "level",
            "instructor_user_id",
            "pilot",
            "version",
        )
    }


def normalize(data, complete=False):
    if not isinstance(data, dict):
        raise ApiError(400, "가격 작성 내용을 확인해주세요.")
    amount = data.get("amount")
    if amount is not None and (type(amount) is not int or not 0 <= amount <= 10000000):
        raise ApiError(400, "가격은 0~10,000,000원의 정수로 입력해주세요.")
    if complete and amount is None:
        raise ApiError(409, "가격은 미정 상태입니다. 무료라면 0원을 직접 입력해주세요.")
    result = {
        "label": text_field(data, "label", minimum=2, maximum=100),
        "amount": amount,
        "currency": choice_field(data, "currency", ("KRW",)),
    }
    start, end = data.get("effectiveAt"), data.get("expiresAt")
    result["effectiveAt"] = canonical_time(start, "가격 시행") if start else None
    result["expiresAt"] = canonical_time(end, "가격 종료") if end else None
    if start and end and result["expiresAt"] <= result["effectiveAt"]:
        raise ApiError(400, "가격 종료는 시행 시각보다 뒤여야 합니다.")
    return result


def price_hash(row, payload, effective=None):
    return digest(
        {
            "courseId": row["course_id"],
            "mode": row["mode"],
            "courseVersion": row["course_version"],
            "payload": payload,
            "effectiveAt": effective,
        }
    )


def sale_control(c, cid, mode):
    row = c.execute(
        "SELECT * FROM product_sale_controls WHERE course_id=? AND mode=?", (cid, mode)
    ).fetchone()
    return (
        dict(row)
        if row
        else {
            "course_id": cid,
            "mode": mode,
            "state": "active",
            "version": 0,
            "reason": "",
            "updated_at": None,
        }
    )


def select_price(c, course, mode, at=None, check_control=True):
    at = at or now_iso()
    control = sale_control(c, course["id"], mode)
    result = {
        "ready": False,
        "status": "missing",
        "classification": "versioned",
        "mode": mode,
        "id": None,
        "edition": None,
        "label": None,
        "amount": None,
        "currency": "KRW",
        "effectiveAt": None,
        "expiresAt": None,
        "effectiveUntil": None,
        "hash": None,
        "controlVersion": control["version"],
        "next": None,
    }
    future = c.execute(
        "SELECT * FROM product_prices WHERE course_id=? AND mode=? AND status IN ('published','withdrawn') AND effective_at>? ORDER BY effective_at,edition LIMIT 1",
        (course["id"], mode, at),
    ).fetchone()
    if future:
        result["next"] = {
            "id": future["id"],
            "edition": future["edition"],
            "effectiveAt": future["effective_at"],
            "status": future["status"],
        }
    row = c.execute(
        "SELECT * FROM product_prices WHERE course_id=? AND mode=? AND status IN ('published','withdrawn') AND effective_at<=? ORDER BY effective_at DESC,edition DESC LIMIT 1",
        (course["id"], mode, at),
    ).fetchone()
    if course["pilot"]:
        result.update(
            classification="pilot",
            status="active",
            amount=course["price"],
            label="파일럿 예시 가격",
            hash=digest([course["id"], course["version"], course["price"], mode]),
        )
        if mode == "live":
            result.update(status="pilot_live", amount=None)
    elif row:
        body = json.loads(row["payload"])
        end = min(
            (
                x
                for x in (row["expires_at"], future["effective_at"] if future else None)
                if x
            ),
            default=None,
        )
        result.update(
            id=row["id"],
            edition=row["edition"],
            label=body["label"],
            hash=row["payload_hash"],
            effectiveAt=row["effective_at"],
            expiresAt=row["expires_at"],
            effectiveUntil=end,
            status="active",
            amount=body["amount"],
        )
        if row["status"] == "withdrawn":
            result["status"] = "withdrawn"
        elif row["expires_at"] and at >= row["expires_at"]:
            result["status"] = "expired"
        elif row["course_version"] != course["version"]:
            result["status"] = "course_changed"
        elif not qualified(c, course["instructor_user_id"]):
            result["status"] = "instructor_unavailable"
    elif future:
        result["status"] = "scheduled"
    if course["status"] != "published":
        result["status"] = "unpublished"
    if check_control and control["state"] == "paused":
        result["status"] = "paused"
    result["ready"] = result["status"] == "active"
    if not result["ready"]:
        result["amount"] = None
    return result


def policy_link(c, policy_id):
    row = c.execute(
        "SELECT * FROM policy_price_links WHERE policy_id=?", (policy_id,)
    ).fetchone()
    return (
        {
            "priceVersionId": row["price_id"],
            "priceHash": row["price_hash"],
            "priceAmount": row["amount"],
            "priceCurrency": row["currency"],
        }
        if row
        else {}
    )


def validate_policy_price(c, course, mode, price_id, at=None):
    row = c.execute(
        "SELECT * FROM product_prices WHERE id=? AND course_id=? AND mode=?",
        (price_id, course["id"], mode),
    ).fetchone()
    if (
        not row
        or row["status"] != "published"
        or row["course_version"] != course["version"]
    ):
        raise ApiError(409, "현재 상품의 발행된 가격 버전을 선택해주세요.")
    if at:
        chosen = select_price(c, course, mode, at, check_control=False)
        if not chosen["ready"] or chosen["id"] != row["id"]:
            raise ApiError(
                409, "상품 제공 문서의 시행 시점에 적용할 가격과 일치하지 않습니다."
            )
    body = json.loads(row["payload"])
    return {
        "priceVersionId": row["id"],
        "priceHash": row["payload_hash"],
        "priceAmount": body["amount"],
        "priceCurrency": body["currency"],
    }


def set_policy_link(c, pid, link):
    if link:
        c.execute(
            "INSERT INTO policy_price_links VALUES(?,?,?,?,?) ON CONFLICT(policy_id) DO UPDATE SET price_id=excluded.price_id,price_hash=excluded.price_hash,amount=excluded.amount,currency=excluded.currency",
            (
                pid,
                link["priceVersionId"],
                link["priceHash"],
                link["priceAmount"],
                link["priceCurrency"],
            ),
        )
    else:
        c.execute("DELETE FROM policy_price_links WHERE policy_id=?", (pid,))


def seal_price(c, order_id, quote, at):
    order = c.execute(
        "SELECT * FROM purchase_orders WHERE id=?", (order_id,)
    ).fetchone()
    snapshot = {
        **quote,
        "courseId": order["course_id"],
        "courseVersion": c.execute(
            "SELECT version FROM courses WHERE id=?", (order["course_id"],)
        ).fetchone()[0],
        "title": order["title"],
    }
    c.execute(
        "INSERT INTO order_price_snapshots VALUES(?,?,?,?,?,?)",
        (
            order_id,
            quote["id"],
            quote["classification"],
            encode(snapshot),
            digest(snapshot),
            at,
        ),
    )


def authorize_price(c, order):
    if (
        c.execute(
            "SELECT 1 FROM payment_policy_checks WHERE order_id=?", (order["id"],)
        ).fetchone()
        or c.execute(
            "SELECT 1 FROM price_legacy_attempts WHERE order_id=?", (order["id"],)
        ).fetchone()
    ):
        return
    sealed = c.execute(
        "SELECT * FROM order_price_snapshots WHERE order_id=?", (order["id"],)
    ).fetchone()
    if not sealed:
        raise ApiError(
            409, "주문 당시 가격 기록이 없습니다. 현재 조건으로 새 주문을 준비해주세요."
        )
    payload = json.loads(sealed["payload"])
    course = c.execute(
        "SELECT * FROM courses WHERE id=?", (order["course_id"],)
    ).fetchone()
    quote = select_price(c, course, order["mode"])
    if (
        digest(payload) != sealed["payload_hash"]
        or not quote["ready"]
        or any(
            quote[k] != payload[k]
            for k in (
                "id",
                "hash",
                "amount",
                "mode",
                "classification",
                "controlVersion",
            )
        )
        or quote["amount"] != order["amount"]
    ):
        raise ApiError(
            409,
            "가격 또는 판매 상태가 바뀌었습니다. 현재 조건으로 새 주문을 준비해주세요.",
        )


class PricingService:
    def __init__(self, app):
        self.app = app

    def event(self, c, user, course, mode, action, reason, pid=None, details=None):
        c.execute(
            "INSERT INTO price_events VALUES(?,?,?,?,?,?,?,?,?)",
            (
                new_id("priceevent"),
                course["id"],
                mode,
                pid,
                user["id"],
                action,
                reason,
                encode(details or {}),
                now_iso(),
            ),
        )
        audit(
            c,
            user,
            "price." + action,
            "course",
            course["id"],
            {"priceId": pid, "mode": mode},
        )

    def view(self, row, admin):
        result = {
            "id": row["id"],
            "courseId": row["course_id"],
            "mode": row["mode"],
            "edition": row["edition"],
            "authorId": row["author_id"],
            "status": row["status"],
            "payload": json.loads(row["payload"]),
            "payloadHash": row["payload_hash"],
            "courseVersion": row["course_version"],
            "courseSnapshot": json.loads(row["course_snapshot"]),
            "effectiveAt": row["effective_at"],
            "expiresAt": row["expires_at"],
            "version": row["version"],
            "reason": row["reason"],
            "reviewNote": row["review_note"],
            "publishedAt": row["published_at"],
            "createdAt": row["created_at"],
        }
        if admin:
            result["approvalReference"] = row["approval_reference"]
        return result

    def listing(self, user, cid, query):
        mode = mode_value(query)
        page, size, offset = page_args(query)
        with self.app.db.connect() as c:
            user, course = managed(c, user, cid)
            where = " WHERE course_id=? AND mode=?"
            args = [cid, mode]
            if query.get("status", [""])[0] in (
                "published",
                "draft",
                "submitted",
                "withdrawn",
                "rejected",
                "cancelled",
            ):
                where += " AND status=?"
                args.append(query["status"][0])
            rows = c.execute(
                "SELECT * FROM product_prices"
                + where
                + " ORDER BY edition DESC LIMIT ? OFFSET ?",
                args + [size, offset],
            ).fetchall()
            control = sale_control(c, cid, mode)
            return {
                "prices": [self.view(r, user["role"] == "admin") for r in rows],
                "total": c.execute(
                    "SELECT COUNT(*) FROM product_prices" + where, args
                ).fetchone()[0],
                "page": page,
                "pageSize": size,
                "current": select_price(c, course, mode),
                "saleControl": {
                    "state": control["state"],
                    "version": control["version"],
                    "reason": control["reason"],
                    "updatedAt": control["updated_at"],
                },
                "events": [
                    {
                        "action": r["action"],
                        "reason": r["reason"],
                        "createdAt": r["created_at"],
                        "details": json.loads(r["details"]),
                    }
                    for r in c.execute(
                        "SELECT * FROM price_events WHERE course_id=? AND mode=? ORDER BY rowid DESC LIMIT 80",
                        (cid, mode),
                    )
                ],
            }

    def exact(self, user, cid, pid):
        with self.app.db.connect() as c:
            user, _ = managed(c, user, cid)
            row = c.execute(
                "SELECT * FROM product_prices WHERE id=? AND course_id=?", (pid, cid)
            ).fetchone()
            if not row:
                raise ApiError(404, "가격 버전을 찾을 수 없습니다.")
            return {"priceVersion": self.view(row, user["role"] == "admin")}

    def new(self, user, cid, data):
        mode = choice_field(data, "mode", ("test", "live"))
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, course = managed(c, user, cid)
            if course["pilot"]:
                raise ApiError(
                    409,
                    "파일럿은 예시 가격을 유지합니다. 정식 강의로 전환한 뒤 가격을 검수해주세요.",
                )
            row = c.execute(
                "SELECT * FROM product_prices WHERE course_id=? AND mode=? AND status IN ('draft','submitted','rejected')",
                (cid, mode),
            ).fetchone()
            if row:
                if user["role"] != "admin" and row["author_id"] != user["id"]:
                    raise ApiError(
                        409, "다른 작성자의 가격 변경안은 관리자가 확인해야 합니다."
                    )
                return {"priceVersion": self.view(row, user["role"] == "admin")}
            old = c.execute(
                "SELECT * FROM product_prices WHERE course_id=? AND mode=? ORDER BY edition DESC LIMIT 1",
                (cid, mode),
            ).fetchone()
            payload = (
                json.loads(old["payload"])
                if old
                else {"label": "강의 판매 가격", "amount": None, "currency": "KRW"}
            )
            payload.update(effectiveAt=None, expiresAt=None)
            pid = new_id("price")
            at = now_iso()
            base = {"course_id": cid, "mode": mode, "course_version": course["version"]}
            c.execute(
                "INSERT INTO product_prices(id,course_id,mode,edition,author_id,payload,payload_hash,course_version,course_snapshot,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pid,
                    cid,
                    mode,
                    old["edition"] + 1 if old else 1,
                    user["id"],
                    encode(payload),
                    price_hash(base, payload),
                    course["version"],
                    encode(course_snapshot(course)),
                    at,
                    at,
                ),
            )
            self.event(
                c,
                user,
                course,
                mode,
                "draft.created",
                "새 가격을 검토합니다. 현재 발행 가격은 유지됩니다.",
                pid,
            )
            return {
                "priceVersion": self.view(
                    c.execute(
                        "SELECT * FROM product_prices WHERE id=?", (pid,)
                    ).fetchone(),
                    user["role"] == "admin",
                )
            }

    def change(self, user, cid, pid, data, save=False):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, course = managed(c, user, cid)
            row = c.execute(
                "SELECT * FROM product_prices WHERE id=? AND course_id=?", (pid, cid)
            ).fetchone()
            if not row:
                raise ApiError(404, "가격 버전을 찾을 수 없습니다.")
            version(data, row)
            action = (
                "save"
                if save
                else choice_field(
                    data,
                    "action",
                    (
                        "submit",
                        "withdraw_submission",
                        "reject",
                        "publish",
                        "withdraw",
                        "cancel",
                        "rebase",
                    ),
                )
            )
            if action in ("publish", "reject", "withdraw") and user["role"] != "admin":
                raise ApiError(
                    403, "가격 발행/반려/철회는 관리자만 처리할 수 있습니다."
                )
            if user["role"] != "admin" and row["author_id"] != user["id"]:
                raise ApiError(403, "본인이 작성한 가격 변경안만 수정할 수 있습니다.")
            allowed = {
                "save": ("draft", "rejected"),
                "submit": ("draft", "rejected"),
                "rebase": ("draft", "rejected"),
                "withdraw_submission": ("submitted",),
                "reject": ("submitted",),
                "publish": ("submitted",),
                "withdraw": ("published",),
                "cancel": ("draft", "rejected"),
            }
            if row["status"] not in allowed[action]:
                raise ApiError(409, "현재 가격 상태에서는 처리할 수 없습니다.")
            if (
                action in ("save", "submit", "publish")
                and row["course_version"] != course["version"]
            ):
                raise ApiError(
                    409, "상품 정보가 바뀌었습니다. 기준 상품을 다시 비교해주세요."
                )
            reason = text_field(
                data, "reason", minimum=0 if save else 5, maximum=1000, optional=save
            )
            at = now_iso()
            if save:
                payload = normalize(data.get("payload"))
                c.execute(
                    "UPDATE product_prices SET payload=?,payload_hash=?,reason=?,status='draft',version=version+1,updated_at=? WHERE id=?",
                    (encode(payload), price_hash(row, payload), reason, at, pid),
                )
            elif action == "rebase":
                if (
                    type(data.get("courseVersion")) is not int
                    or data["courseVersion"] != course["version"]
                ):
                    raise ApiError(
                        409, "비교한 상품 버전이 달라졌습니다. 다시 확인해주세요."
                    )
                base = dict(row)
                base["course_version"] = course["version"]
                c.execute(
                    "UPDATE product_prices SET course_version=?,course_snapshot=?,payload_hash=?,status='draft',version=version+1,updated_at=? WHERE id=?",
                    (
                        course["version"],
                        encode(course_snapshot(course)),
                        price_hash(base, json.loads(row["payload"])),
                        at,
                        pid,
                    ),
                )
            elif action == "submit":
                normalize(json.loads(row["payload"]), True)
                c.execute(
                    "UPDATE product_prices SET status='submitted',reason=?,submitted_at=?,version=version+1,updated_at=? WHERE id=?",
                    (reason, at, at, pid),
                )
            elif action == "publish":
                payload = normalize(json.loads(row["payload"]), True)
                if (
                    course["pilot"]
                    or course["status"] != "published"
                    or not qualified(c, course["instructor_user_id"])
                    or not c.execute(
                        "SELECT 1 FROM course_lessons WHERE course_id=? AND status='published'",
                        (cid,),
                    ).fetchone()
                ):
                    raise ApiError(
                        409, "발행된 정식 강의·활성 담당자·공개 차시를 확인해주세요."
                    )
                start = payload["effectiveAt"] or at
                end = payload["expiresAt"]
                if start < at or (end and end <= start):
                    raise ApiError(
                        400,
                        "시행/종료 시각을 현재 이후로 확인해주세요. 바로 시행은 비워주세요.",
                    )
                if c.execute(
                    "SELECT 1 FROM product_prices WHERE course_id=? AND mode=? AND effective_at=? AND status IN ('published','withdrawn')",
                    (cid, row["mode"], start),
                ).fetchone():
                    raise ApiError(
                        409,
                        "같은 시행 시각의 가격이 있습니다. 다른 시각을 선택해주세요.",
                    )
                ref = text_field(data, "approvalReference", minimum=5, maximum=2000)
                c.execute(
                    "UPDATE product_prices SET status='published',effective_at=?,expires_at=?,payload_hash=?,review_note=?,approval_reference=?,reviewer_id=?,published_at=?,version=version+1,updated_at=? WHERE id=?",
                    (
                        start,
                        end,
                        price_hash(row, payload, start),
                        reason,
                        ref,
                        user["id"],
                        at,
                        at,
                        pid,
                    ),
                )
                notify(
                    c,
                    course["instructor_user_id"],
                    "가격 검수가 반영되었습니다.",
                    "상품 제공 문서와 판매 준비 상태를 확인해주세요.",
                    "#/studio/course/" + cid + "/pricing?mode=" + row["mode"],
                    event_key="price.published:" + pid,
                    category="operation",
                    reference_kind="course_staff",
                    reference_id=cid,
                    group_key="course:" + cid,
                )
            else:
                status = {
                    "withdraw_submission": "draft",
                    "reject": "rejected",
                    "withdraw": "withdrawn",
                    "cancel": "cancelled",
                }[action]
                c.execute(
                    "UPDATE product_prices SET status=?,review_note=?,withdrawn_at=?,version=version+1,updated_at=? WHERE id=?",
                    (status, reason, at if action == "withdraw" else None, at, pid),
                )
            self.event(c, user, course, row["mode"], action, reason, pid)
            return {
                "priceVersion": self.view(
                    c.execute(
                        "SELECT * FROM product_prices WHERE id=?", (pid,)
                    ).fetchone(),
                    user["role"] == "admin",
                )
            }

    def control(self, user, cid, data):
        mode = choice_field(data, "mode", ("test", "live"))
        action = choice_field(data, "action", ("pause", "resume"))
        reason = text_field(data, "reason", minimum=5, maximum=1000)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, course = managed(c, user, cid, True)
            old = sale_control(c, cid, mode)
            if (
                type(data.get("version")) is not int
                or data["version"] != old["version"]
            ):
                raise ApiError(
                    409, "판매 상태가 바뀌었습니다. 현재 상태를 다시 확인해주세요."
                )
            state = "paused" if action == "pause" else "active"
            if old["state"] == state:
                raise ApiError(409, "이미 같은 판매 상태입니다.")
            c.execute(
                "INSERT INTO product_sale_controls VALUES(?,?,?,?,?,?,?) ON CONFLICT(course_id,mode) DO UPDATE SET state=excluded.state,version=excluded.version,reason=excluded.reason,actor_id=excluded.actor_id,updated_at=excluded.updated_at",
                (cid, mode, state, old["version"] + 1, reason, user["id"], now_iso()),
            )
            self.event(
                c,
                user,
                course,
                mode,
                "sale." + action,
                reason,
                details={
                    "previousVersion": old["version"],
                    "version": old["version"] + 1,
                },
            )
            return {
                "saleControl": {
                    "state": state,
                    "version": old["version"] + 1,
                    "reason": reason,
                }
            }

    def readiness(self, user, cid, query):
        from .policies import conditions_bundle, public_bundle

        mode = mode_value(query)
        with self.app.db.connect() as c:
            user, course = managed(c, user, cid)
            if course["status"] != "published":
                return {
                    "readiness": {
                        "ready": False,
                        "blockers": [
                            {
                                "title": "상품 발행",
                                "message": "강의 메타데이터·담당자·차시를 확인한 후 발행해주세요.",
                            }
                        ],
                        "price": select_price(c, course, mode),
                    }
                }
            bundle = conditions_bundle(c, "checkout", mode, cid)
            return {
                "readiness": {
                    **public_bundle(bundle, False),
                    "price": select_price(c, course, mode),
                }
            }

    def order_snapshot(self, user, oid):
        with self.app.db.connect() as c:
            actor = (
                c.execute(
                    "SELECT * FROM users WHERE id=? AND status='active'", (user["id"],)
                ).fetchone()
                if user
                else None
            )
            if not actor:
                raise ApiError(401, "로그인 후 주문 가격을 확인해주세요.")
            order = c.execute(
                "SELECT * FROM purchase_orders WHERE id=? AND (user_id=? OR ?='admin')",
                (oid, actor["id"], actor["role"]),
            ).fetchone()
            if not order:
                raise ApiError(404, "조회할 수 있는 주문을 찾을 수 없습니다.")
            row = c.execute(
                "SELECT * FROM order_price_snapshots WHERE order_id=?", (oid,)
            ).fetchone()
            return {
                "priceSnapshot": (
                    {
                        "recorded": True,
                        "payload": json.loads(row["payload"]),
                        "payloadHash": row["payload_hash"],
                        "sealedAt": row["sealed_at"],
                    }
                    if row
                    else {"recorded": False, "orderId": oid}
                )
            }
