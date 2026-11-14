"""Versioned documents, explicit consent and immutable purchase conditions.

No commercial terms are seeded here. Operator-entered live approval references,
test documents and legacy payment attempts are separate kinds of evidence.
"""

from datetime import datetime, timedelta
import hashlib
import json
from .contracts import readiness as contract_readiness
from .pricing import select_price, policy_link, validate_policy_price, set_policy_link
from .order_products import course_product

from ..access import canonical_time
from ..core import ApiError, choice_field, new_id, now_iso, text_field
from .common import audit, page_args, require_role, version

KINDS = {
    "terms": "이용 조건",
    "privacy": "개인정보 안내",
    "refund": "환불 안내",
    "course_terms": "상품 제공 조건",
    "content_rights": "콘텐츠 권리 증빙",
}
PUBLIC_KINDS = ("terms", "privacy", "refund", "course_terms")
PAYMENT_WINDOW_SECONDS = 1800
HASH_FIELDS = (
    "kind",
    "mode",
    "course_id",
    "course_version",
    "edition",
    "title",
    "summary",
    "body",
    "effective_at",
    "expires_at",
)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def content_hash(row, price=None):
    value = {key: row[key] for key in HASH_FIELDS}
    if price:
        value["price"] = price
    return digest(value)


def timing(row, at):
    if row["status"] != "published":
        return row["status"]
    if row["effective_at"] > at:
        return "scheduled"
    if row["expires_at"] is not None and row["expires_at"] <= at:
        return "expired"
    return "effective"


def policy_view(row, *, operator=False, body=True, at=None, price=None):
    if row is None:
        return None
    at = at or now_iso()
    result = {
        "id": row["id"],
        "kind": row["kind"],
        "kindLabel": KINDS[row["kind"]],
        "mode": row["mode"],
        "courseId": row["course_id"],
        "courseVersion": row["course_version"],
        "edition": row["edition"],
        "title": row["title"],
        "summary": row["summary"],
        "status": row["status"],
        "timing": timing(row, at),
        "version": row["version"],
        "effectiveAt": row["effective_at"],
        "expiresAt": row["expires_at"],
        "publishedAt": row["published_at"],
        "withdrawnAt": row["withdrawn_at"],
        "withdrawalReason": row["withdrawal_reason"],
        "contentHash": row["content_hash"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }
    result.update(price or {})
    if body and (row["kind"] in PUBLIC_KINDS or operator):
        result["body"] = row["body"]
    if operator:
        result["approvalReference"] = row["approval_reference"]
    return result


def period_fields(data, previous=None):
    start = data.get("effectiveAt", previous["effective_at"] if previous else None)
    end = data.get("expiresAt", previous["expires_at"] if previous else None)
    start = None if start is None or start == "" else canonical_time(start, "시행 시각")
    end = None if end is None or end == "" else canonical_time(end, "만료 시각")
    if start and end and end <= start:
        raise ApiError(400, "만료 시각은 시행 시각보다 뒤여야 합니다.")
    return start, end


def current_document(c, kind, mode, course_id, at):
    row = c.execute(
        "SELECT * FROM policy_versions WHERE kind=? AND mode=? AND course_id IS ? AND status IN ('published','withdrawn') AND effective_at<=? ORDER BY edition DESC LIMIT 1",
        (kind, mode, course_id, at),
    ).fetchone()
    if row is None:
        row = c.execute(
            "SELECT * FROM policy_versions WHERE kind=? AND mode=? AND course_id IS ? AND status IN ('published','withdrawn') AND effective_at>? ORDER BY effective_at,edition DESC LIMIT 1",
            (kind, mode, course_id, at),
        ).fetchone()
    return row


def selected_version(c, row, at):
    selected = current_document(c, row["kind"], row["mode"], row["course_id"], at)
    return bool(selected and selected["id"] == row["id"])


def conditions_bundle(c, context, mode, course_id=None, at=None):
    at = at or now_iso()
    if context not in ("public", "account", "checkout") or mode not in ("test", "live"):
        raise ApiError(400, "정책 범위와 환경을 확인해주세요.")
    course = None
    if context == "checkout":
        course = c.execute(
            "SELECT * FROM courses WHERE id=? AND status='published'", (course_id,)
        ).fetchone()
        if not course:
            raise ApiError(404, "현재 판매 중인 강의를 찾을 수 없습니다.")
    kinds = (
        ("terms", "privacy")
        if context == "account"
        else (
            ("terms", "privacy", "refund", "course_terms", "content_rights")
            if context == "checkout"
            else ("terms", "privacy", "refund")
        )
    )
    documents, blockers = [], []
    quote = select_price(c, course, mode, at) if course else None
    if quote and not quote["ready"]:
        blockers.append(
            {
                "kind": "price",
                "title": "상품 가격",
                "code": quote["status"],
                "message": "현재 적용할 가격과 판매 상태를 확인해주세요.",
            }
        )
    for kind in kinds:
        row = current_document(
            c,
            kind,
            mode,
            course_id if kind in ("course_terms", "content_rights") else None,
            at,
        )
        code = "missing" if row is None else timing(row, at)
        if (
            code == "effective"
            and course
            and row["course_id"]
            and row["course_version"] != course["version"]
        ):
            code = "course_changed"
        if row is not None:
            documents.append(row)
        if (
            code == "effective"
            and course
            and kind == "course_terms"
            and not course["pilot"]
        ):
            anchor = policy_link(c, row["id"])
            if (
                not anchor
                or anchor["priceVersionId"] != quote["id"]
                or anchor["priceHash"] != quote["hash"]
            ):
                code = "price_changed"
        if code != "effective":
            blockers.append(
                {
                    "kind": kind,
                    "title": KINDS[kind],
                    "code": code,
                    "message": {
                        "missing": "발행된 버전이 없습니다.",
                        "scheduled": "아직 시행 시각 전입니다.",
                        "expired": "적용 버전의 유효기간이 끝났습니다.",
                        "withdrawn": "적용 버전이 철회되었습니다.",
                        "course_changed": "상품 정보가 바뀌어 새 버전 확인이 필요합니다.",
                        "price_changed": "현재 적용 가격에 연결된 새 상품 제공 문서가 필요합니다.",
                    }.get(code, "발행 조건을 확인해주세요."),
                }
            )
    snapshot = (
        None
        if course is None
        else {
            "id": course["id"],
            "title": course["title"],
            "amount": quote["amount"],
            "priceVersionId": quote["id"],
            "priceEdition": quote["edition"],
            "priceHash": quote["hash"],
            "currency": "KRW",
            "version": course["version"],
            "pilot": bool(course["pilot"]),
            "product": course_product(course, quote, mode),
        }
    )
    contract = contract_readiness(c, course_id, mode, at) if course else None
    if contract and not contract["ready"]:
        blockers.append(
            {
                "kind": "partner_contract",
                "title": "상품 제공 준비",
                "code": "unavailable",
                "message": contract["message"],
            }
        )
    fingerprint = (
        digest(
            {
                "context": context,
                "mode": mode,
                "contract": contract["digest"] if contract else None,
                "price": quote,
                "course": snapshot,
                "documents": sorted((r["id"], r["content_hash"]) for r in documents),
            }
        )
        if not blockers
        else None
    )
    return {
        "context": context,
        "mode": mode,
        "courseId": course_id if course else None,
        "course": snapshot,
        "asOf": at,
        "ready": not blockers,
        "fingerprint": fingerprint,
        "rows": documents,
        "price": quote,
        "priceLinks": {r["id"]: policy_link(c, r["id"]) for r in documents},
        "blockers": blockers,
    }


def public_bundle(bundle, operator=False):
    return {
        **{
            key: value
            for key, value in bundle.items()
            if key not in ("rows", "priceLinks")
        },
        "documents": [
            policy_view(
                r,
                operator=operator,
                at=bundle["asOf"],
                price=bundle["priceLinks"].get(r["id"]),
            )
            for r in bundle["rows"]
            if r["kind"] in PUBLIC_KINDS
        ],
        **(
            {
                "rights": policy_view(
                    next(
                        (r for r in bundle["rows"] if r["kind"] == "content_rights"),
                        None,
                    ),
                    operator=True,
                    at=bundle["asOf"],
                )
            }
            if operator
            else {}
        ),
        "requiredPolicyIds": [
            r["id"] for r in bundle["rows"] if r["kind"] in PUBLIC_KINDS
        ],
    }


def explicit_selection(bundle, data):
    if not bundle["ready"]:
        raise ApiError(
            409,
            "필수 정책과 제공 조건을 준비 중입니다. 원문과 준비 상태를 확인해주세요.",
        )
    if data.get("policyDigest") != bundle["fingerprint"]:
        raise ApiError(
            409, "확인한 정책이 변경되었습니다. 최신 원문을 읽고 다시 동의해주세요."
        )
    accepted = data.get("acceptedPolicyIds")
    expected = {r["id"] for r in bundle["rows"] if r["kind"] in PUBLIC_KINDS}
    if (
        not isinstance(accepted, list)
        or any(not isinstance(x, str) for x in accepted)
        or len(accepted) != len(expected)
        or set(accepted) != expected
    ):
        raise ApiError(400, "필수 정책을 각각 확인하고 명시적으로 동의해주세요.")


def record_consents(c, user_id, bundle, order_id=None):
    for row in bundle["rows"]:
        if row["kind"] not in PUBLIC_KINDS:
            continue
        c.execute(
            "INSERT OR IGNORE INTO consents(id,user_id,policy_version_id,context,order_id,content_hash,accepted_at) VALUES(?,?,?,?,?,?,?)",
            (
                new_id("consent"),
                user_id,
                row["id"],
                "purchase" if order_id else "account",
                order_id,
                row["content_hash"],
                bundle["asOf"],
            ),
        )


def seal_order(c, order_id, user_id, bundle):
    deadline = (
        (
            datetime.fromisoformat(bundle["asOf"].replace("Z", "+00:00"))
            + timedelta(seconds=PAYMENT_WINDOW_SECONDS)
        )
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    c.execute(
        "INSERT INTO order_policy_sets VALUES(?,?,?,?,?)",
        (
            order_id,
            bundle["fingerprint"],
            json.dumps(bundle["course"], ensure_ascii=False),
            bundle["asOf"],
            deadline,
        ),
    )
    for row in bundle["rows"]:
        c.execute(
            "INSERT INTO order_policy_documents VALUES(?,?,?,?,?)",
            (
                order_id,
                row["id"],
                row["kind"],
                "public" if row["kind"] in PUBLIC_KINDS else "operator",
                row["content_hash"],
            ),
        )
    record_consents(c, user_id, bundle, order_id)


def authorize_payment(c, order):
    existing = c.execute(
        "SELECT * FROM payment_policy_checks WHERE order_id=?", (order["id"],)
    ).fetchone()
    if existing:
        return
    if (
        c.execute(
            "SELECT 1 FROM legacy_payment_attempts WHERE order_id=?", (order["id"],)
        ).fetchone()
        and order["payment_key"]
        and order["status"] in ("pending", "confirming")
    ):
        c.execute(
            "INSERT INTO payment_policy_checks VALUES(?,?,'legacy_inflight','')",
            (order["id"], now_iso()),
        )
        return
    sealed = c.execute(
        "SELECT * FROM order_policy_sets WHERE order_id=?", (order["id"],)
    ).fetchone()
    if not sealed:
        raise ApiError(
            409,
            "이 주문은 구매 조건과 동의 기록이 없습니다. 최신 조건을 확인한 새 주문으로 진행해주세요.",
        )
    at = now_iso()
    if at >= sealed["payment_deadline"]:
        raise ApiError(
            409,
            "결제 준비 시간이 끝났습니다. 최신 조건을 확인해 새 주문을 준비해주세요.",
        )
    course = c.execute(
        "SELECT * FROM courses WHERE id=?", (order["course_id"],)
    ).fetchone()
    snapshot = json.loads(sealed["course_snapshot"])
    if (
        not course
        or course["status"] != "published"
        or course["version"] != snapshot["version"]
    ):
        raise ApiError(
            409,
            "상품 정보가 변경되었습니다. 최신 제공 조건으로 새 주문을 준비해주세요.",
        )
    rows = c.execute(
        "SELECT p.*,d.visibility,d.content_hash AS sealed_hash FROM order_policy_documents d JOIN policy_versions p ON p.id=d.policy_version_id WHERE d.order_id=?",
        (order["id"],),
    ).fetchall()
    if {r["kind"] for r in rows} != set(PUBLIC_KINDS) | {"content_rights"} or any(
        timing(r, at) != "effective"
        or r["mode"] != order["mode"]
        or r["content_hash"] != r["sealed_hash"]
        for r in rows
    ):
        raise ApiError(
            409,
            "주문에 적용된 필수 정책이 철회되었거나 만료되었습니다. 새 조건을 확인해주세요.",
        )
    accepted = c.execute(
        "SELECT policy_version_id,content_hash FROM consents WHERE order_id=? AND user_id=? AND context='purchase'",
        (order["id"], order["user_id"]),
    ).fetchall()
    if {(r[0], r[1]) for r in accepted} != {
        (r["id"], r["content_hash"]) for r in rows if r["kind"] in PUBLIC_KINDS
    }:
        raise ApiError(
            409, "구매 동의 기록을 확인할 수 없습니다. 운영자에게 문의해주세요."
        )
    c.execute(
        "INSERT INTO payment_policy_checks VALUES(?,?,'versioned',?)",
        (order["id"], at, sealed["fingerprint"]),
    )


def order_policy_summary(c, order_id):
    row = c.execute(
        "SELECT * FROM order_policy_sets WHERE order_id=?", (order_id,)
    ).fetchone()
    check = c.execute(
        "SELECT classification,checked_at FROM payment_policy_checks WHERE order_id=?",
        (order_id,),
    ).fetchone()
    return {
        "status": "recorded" if row else "unrecorded",
        "fingerprint": row["fingerprint"] if row else None,
        "sealedAt": row["sealed_at"] if row else None,
        "paymentDeadline": row["payment_deadline"] if row else None,
        "paymentCheck": (
            {
                "classification": check["classification"],
                "checkedAt": check["checked_at"],
            }
            if check
            else None
        ),
    }


class PolicyService:
    def __init__(self, app):
        self.app = app

    def current(self, query, operator=None):
        context = query.get("context", ["public"])[0]
        mode = query.get("mode", ["live"])[0]
        course_id = query.get("courseId", [None])[0]
        with self.app.db.connect() as c:
            if operator is not None:
                require_role(c, operator, "admin")
            bundle = conditions_bundle(c, context, mode, course_id)
            return {"policies": public_bundle(bundle, operator is not None)}

    def document(self, user, policy_id):
        with self.app.db.connect() as c:
            row = c.execute(
                "SELECT * FROM policy_versions WHERE id=?", (policy_id,)
            ).fetchone()
            operator = bool(user and user["role"] == "admin")
            if not row or (
                not operator
                and (row["status"] == "draft" or row["kind"] not in PUBLIC_KINDS)
            ):
                raise ApiError(404, "조회할 수 있는 정책 버전을 찾을 수 없습니다.")
            at = now_iso()
            return {
                "policy": {
                    **policy_view(
                        row, operator=operator, at=at, price=policy_link(c, row["id"])
                    ),
                    "current": selected_version(c, row, at),
                }
            }

    def listing(self, user, query):
        page, limit, offset = page_args(query)
        with self.app.db.connect() as c:
            require_role(c, user, "admin")
            filters, args = [], []
            for key, column, allowed in (
                ("kind", "kind", KINDS),
                ("mode", "mode", ("test", "live")),
                ("status", "status", ("draft", "published", "withdrawn")),
            ):
                value = query.get(key, [""])[0]
                if value:
                    if value not in allowed:
                        raise ApiError(400, "정책 필터를 확인해주세요.")
                    filters.append(column + "=?")
                    args.append(value)
            course = query.get("courseId", [""])[0]
            if course:
                filters.append("course_id=?")
                args.append(course)
            condition = " WHERE " + " AND ".join(filters) if filters else ""
            total = c.execute(
                "SELECT COUNT(*) FROM policy_versions" + condition, args
            ).fetchone()[0]
            rows = c.execute(
                "SELECT * FROM policy_versions"
                + condition
                + " ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?",
                args + [limit, offset],
            ).fetchall()
            at = now_iso()
            return {
                "policies": [
                    {
                        **policy_view(
                            row,
                            operator=True,
                            body=False,
                            at=at,
                            price=policy_link(c, row["id"]),
                        ),
                        "current": selected_version(c, row, at),
                    }
                    for row in rows
                ],
                "total": total,
                "page": page,
                "pageSize": limit,
            }

    def save(self, user, data, policy_id=None):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            require_role(c, user, "admin")
            row = (
                c.execute(
                    "SELECT * FROM policy_versions WHERE id=?", (policy_id,)
                ).fetchone()
                if policy_id
                else None
            )
            if policy_id and not row:
                raise ApiError(404, "정책을 찾을 수 없습니다.")
            if row:
                version(data, row)
                if row["status"] != "draft":
                    raise ApiError(
                        409,
                        "발행된 원문은 수정할 수 없습니다. 새 버전 초안을 만들어주세요.",
                    )
                kind, mode, course_id = row["kind"], row["mode"], row["course_id"]
                for name, expected in (
                    ("kind", kind),
                    ("mode", mode),
                    ("courseId", course_id or ""),
                ):
                    if name in data and (data[name] or "") != expected:
                        raise ApiError(
                            400, "기존 초안의 종류·환경·강의 범위는 바꿀 수 없습니다."
                        )
                edition = row["edition"]
            else:
                kind = choice_field(data, "kind", KINDS)
                mode = choice_field(data, "mode", ("test", "live"))
                course_id = (
                    text_field(data, "courseId", maximum=100, optional=True) or None
                )
                if (kind in ("course_terms", "content_rights")) != bool(course_id):
                    raise ApiError(
                        400,
                        "상품 조건/권리 증빙에는 대상 강의가 필요하며 전역 정책에는 강의를 지정하지 않습니다.",
                    )
                edition = c.execute(
                    "SELECT COALESCE(MAX(edition),0)+1 FROM policy_versions WHERE kind=? AND mode=? AND course_id IS ?",
                    (kind, mode, course_id),
                ).fetchone()[0]
            course = (
                c.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
                if course_id
                else None
            )
            if course_id and not course:
                raise ApiError(404, "대상 강의를 찾을 수 없습니다.")
            link = {}
            if course and kind == "course_terms" and not course["pilot"]:
                oldlink = policy_link(c, policy_id) if policy_id else {}
                selected = data.get("priceVersionId", oldlink.get("priceVersionId"))
                if not selected:
                    raise ApiError(
                        400, "상품 제공 문서에 적용할 발행 가격을 선택해주세요."
                    )
                link = validate_policy_price(c, course, mode, selected)
            title = text_field(data, "title", minimum=3, maximum=120)
            summary = text_field(data, "summary", maximum=1000, optional=True)
            body = text_field(data, "body", minimum=20, maximum=12000)
            start, end = period_fields(data, row)
            values = {
                "kind": kind,
                "mode": mode,
                "course_id": course_id,
                "course_version": course["version"] if course else None,
                "edition": edition,
                "title": title,
                "summary": summary,
                "body": body,
                "effective_at": start,
                "expires_at": end,
            }
            timestamp = now_iso()
            if row:
                c.execute(
                    "UPDATE policy_versions SET title=?,summary=?,body=?,effective_at=?,expires_at=?,course_version=?,content_hash=?,version=version+1,updated_at=?,updated_by=? WHERE id=?",
                    (
                        title,
                        summary,
                        body,
                        start,
                        end,
                        values["course_version"],
                        content_hash(values, link),
                        timestamp,
                        user["id"],
                        policy_id,
                    ),
                )
            else:
                policy_id = new_id("policy")
                c.execute(
                    "INSERT INTO policy_versions(id,kind,mode,course_id,course_version,edition,title,summary,body,effective_at,expires_at,content_hash,created_by,updated_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        policy_id,
                        kind,
                        mode,
                        course_id,
                        values["course_version"],
                        edition,
                        title,
                        summary,
                        body,
                        start,
                        end,
                        content_hash(values, link),
                        user["id"],
                        user["id"],
                        timestamp,
                        timestamp,
                    ),
                )
            set_policy_link(c, policy_id, link)
            audit(
                c,
                user,
                "policy.draft.saved",
                "policy",
                policy_id,
                {"kind": kind, "mode": mode, "courseId": course_id, "edition": edition},
            )
            return {
                "policy": policy_view(
                    c.execute(
                        "SELECT * FROM policy_versions WHERE id=?", (policy_id,)
                    ).fetchone(),
                    operator=True,
                    price=policy_link(c, policy_id),
                )
            }

    def publish(self, user, policy_id, data):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            require_role(c, user, "admin")
            row = c.execute(
                "SELECT * FROM policy_versions WHERE id=?", (policy_id,)
            ).fetchone()
            if not row:
                raise ApiError(404, "정책을 찾을 수 없습니다.")
            version(data, row)
            action = choice_field(data, "action", ("publish", "withdraw"))
            reason = text_field(data, "reason", minimum=5, maximum=2000)
            at = now_iso()
            if action == "withdraw":
                if row["status"] != "published":
                    raise ApiError(409, "발행된 버전만 철회할 수 있습니다.")
                c.execute(
                    "UPDATE policy_versions SET status='withdrawn',withdrawn_at=?,withdrawal_reason=?,version=version+1,updated_at=?,updated_by=? WHERE id=?",
                    (at, reason, at, user["id"], policy_id),
                )
            else:
                if row["status"] != "draft":
                    raise ApiError(409, "초안만 새로 발행할 수 있습니다.")
                if c.execute(
                    "SELECT 1 FROM policy_versions WHERE kind=? AND mode=? AND course_id IS ? AND edition>? AND status!='draft'",
                    (row["kind"], row["mode"], row["course_id"], row["edition"]),
                ).fetchone():
                    raise ApiError(
                        409,
                        "더 높은 버전이 이미 발행되었습니다. 새 버전 초안을 만들어주세요.",
                    )
                start = row["effective_at"] or at
                if start < at or (row["expires_at"] and row["expires_at"] <= start):
                    raise ApiError(
                        400,
                        "시행/만료 시각이 지났습니다. 초안의 시각을 다시 확인해주세요.",
                    )
                if row["course_id"]:
                    course = c.execute(
                        "SELECT * FROM courses WHERE id=?", (row["course_id"],)
                    ).fetchone()
                    if course["version"] != row["course_version"]:
                        raise ApiError(
                            409,
                            "대상 상품이 바뀌었습니다. 최신 상품을 확인한 뒤 초안을 다시 저장해주세요.",
                        )
                link = policy_link(c, policy_id)
                if row["kind"] == "course_terms" and not course["pilot"]:
                    if not link:
                        raise ApiError(
                            409, "상품 제공 문서의 가격 버전을 다시 확인해주세요."
                        )
                    validate_policy_price(
                        c, course, row["mode"], link["priceVersionId"], start
                    )
                approval = text_field(
                    data,
                    "approvalReference",
                    minimum=5,
                    maximum=2000,
                    optional=row["mode"] == "test",
                )
                values = dict(row)
                values["effective_at"] = start
                c.execute(
                    "UPDATE policy_versions SET status='published',effective_at=?,approval_reference=?,published_at=?,content_hash=?,version=version+1,updated_at=?,updated_by=? WHERE id=?",
                    (
                        start,
                        approval,
                        at,
                        content_hash(values, link),
                        at,
                        user["id"],
                        policy_id,
                    ),
                )
            audit(
                c,
                user,
                "policy." + action,
                "policy",
                policy_id,
                {
                    "reason": reason,
                    "edition": row["edition"],
                    "mode": row["mode"],
                    "kind": row["kind"],
                },
            )
            return {
                "policy": policy_view(
                    c.execute(
                        "SELECT * FROM policy_versions WHERE id=?", (policy_id,)
                    ).fetchone(),
                    operator=True,
                    at=at,
                    price=policy_link(c, policy_id),
                )
            }

    def consent(self, user, data):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        mode = choice_field(data, "mode", ("test", "live"))
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            bundle = conditions_bundle(c, "account", mode)
            explicit_selection(bundle, data)
            record_consents(c, user["id"], bundle)
            return {
                "acceptedPolicyIds": [r["id"] for r in bundle["rows"]],
                "policyDigest": bundle["fingerprint"],
            }

    def consent_history(self, user, query):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        page, limit, offset = page_args(query)
        with self.app.db.connect() as c:
            rows = c.execute(
                "SELECT s.*,p.title,p.kind,p.mode,p.edition FROM consents s JOIN policy_versions p ON p.id=s.policy_version_id WHERE s.user_id=? ORDER BY s.accepted_at DESC,s.rowid DESC LIMIT ? OFFSET ?",
                (user["id"], limit, offset),
            ).fetchall()
            return {
                "consents": [
                    {
                        "id": r["id"],
                        "policyId": r["policy_version_id"],
                        "title": r["title"],
                        "kind": r["kind"],
                        "mode": r["mode"],
                        "edition": r["edition"],
                        "context": r["context"],
                        "orderId": r["order_id"],
                        "acceptedAt": r["accepted_at"],
                        "contentHash": r["content_hash"],
                    }
                    for r in rows
                ],
                "page": page,
                "pageSize": limit,
                "total": c.execute(
                    "SELECT COUNT(*) FROM consents WHERE user_id=?", (user["id"],)
                ).fetchone()[0],
            }

    def order_conditions(self, user, order_id, operator=False):
        if not user:
            raise ApiError(401, "로그인 후 이용해주세요.")
        with self.app.db.connect() as c:
            if operator:
                require_role(c, user, "admin")
            order = c.execute(
                "SELECT * FROM purchase_orders WHERE id=?"
                + ("" if operator else " AND user_id=?"),
                (order_id,) if operator else (order_id, user["id"]),
            ).fetchone()
            if not order:
                raise ApiError(404, "주문을 찾을 수 없습니다.")
            sealed = c.execute(
                "SELECT * FROM order_policy_sets WHERE order_id=?", (order_id,)
            ).fetchone()
            documents = c.execute(
                "SELECT p.*,d.visibility FROM order_policy_documents d JOIN policy_versions p ON p.id=d.policy_version_id WHERE d.order_id=?"
                + ("" if operator else " AND d.visibility='public'")
                + " ORDER BY p.kind",
                (order_id,),
            ).fetchall()
            accepted = c.execute(
                "SELECT policy_version_id,accepted_at,content_hash FROM consents WHERE order_id=? AND user_id=?",
                (order_id, order["user_id"]),
            ).fetchall()
            return {
                "conditions": {
                    "orderId": order_id,
                    **order_policy_summary(c, order_id),
                    "course": json.loads(sealed["course_snapshot"]) if sealed else None,
                    "documents": [
                        policy_view(r, operator=operator, price=policy_link(c, r["id"]))
                        for r in documents
                    ],
                    "consents": [
                        {
                            "policyId": r["policy_version_id"],
                            "acceptedAt": r["accepted_at"],
                            "contentHash": r["content_hash"],
                        }
                        for r in accepted
                    ],
                }
            }
