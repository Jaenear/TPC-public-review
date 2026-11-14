"""Private records of externally agreed terms; no signatures or payouts.

Bindings are half-open intervals. Order snapshots are immutable and are never
backfilled from today's terms. Policy and payment code call the same readiness
function while owning their SQLite write transaction.
"""

import hashlib
import json
from ..core import ApiError, now_iso, new_id, text_field, choice_field
from ..access import canonical_time
from .common import roles, version, page_args, audit, notify

MODELS = ("unresolved", "company", "joint", "coach")
TEXT_FIELDS = (
    "coachContribution",
    "companyContribution",
    "deductionPolicy",
    "taxPolicy",
    "refundPolicy",
    "rightsPolicy",
    "settlementPolicy",
    "body",
)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def current_actor(c, user, admin=False):
    row = (
        c.execute(
            "SELECT * FROM users WHERE id=? AND status='active'", (user["id"],)
        ).fetchone()
        if user
        else None
    )
    if row is None:
        raise ApiError(401, "로그인 후 계약 기록을 확인해주세요.")
    if row["role"] != "admin" and (admin or "coach" not in roles(c, row)):
        raise ApiError(403, "계약 기록에 접근할 권한이 없습니다.")
    return row


def qualified(c, user_id):
    row = c.execute(
        "SELECT * FROM users WHERE id=? AND status='active'", (user_id,)
    ).fetchone()
    return bool(row and (row["role"] == "admin" or "coach" in roles(c, row)))


def managed(c, user, contract_id, admin=False):
    user = current_actor(c, user, admin)
    row = c.execute(
        "SELECT p.*,u.name AS partner_name FROM partner_contracts p JOIN users u ON u.id=p.partner_user_id WHERE p.id=?",
        (contract_id,),
    ).fetchone()
    if not row or (user["role"] != "admin" and row["partner_user_id"] != user["id"]):
        raise ApiError(404, "조회할 수 있는 계약을 찾을 수 없습니다.")
    return user, row


def normalize(data, complete=False):
    if not isinstance(data, dict):
        raise ApiError(400, "계약 작성 내용을 확인해주세요.")
    result = {
        "title": text_field(data, "title", minimum=2, maximum=150),
        "productionModel": choice_field(data, "productionModel", MODELS),
        "currency": choice_field(data, "currency", ("KRW",)),
        "revenueBasis": choice_field(
            data, "revenueBasis", ("unresolved", "gross_collections", "net_collections")
        ),
    }
    for name in TEXT_FIELDS:
        result[name] = text_field(
            data,
            name,
            minimum=20 if complete else 0,
            maximum=12000 if name == "body" else 3000,
            optional=not complete,
        )
    for name in ("coachShareBps", "companyShareBps"):
        value = data.get(name)
        if value is not None and (type(value) is not int or not 0 <= value <= 10000):
            raise ApiError(
                400, "배분율은 0~100% 범위에서 소수 둘째 자리까지 입력해주세요."
            )
        if complete and value is None:
            raise ApiError(
                409, "합의한 배분율을 모두 입력해주세요. 제안값은 확정하지 않습니다."
            )
        result[name] = value
    start, end = data.get("effectiveAt"), data.get("expiresAt")
    result["effectiveAt"] = canonical_time(start, "계약 시행 시각") if start else None
    result["expiresAt"] = canonical_time(end, "계약 종료 시각") if end else None
    if end and (not start or result["expiresAt"] <= result["effectiveAt"]):
        raise ApiError(400, "계약 종료는 시행 시각보다 뒤여야 합니다.")
    if complete and (
        not start
        or result["productionModel"] == "unresolved"
        or result["revenueBasis"] == "unresolved"
        or result["coachShareBps"] + result["companyShareBps"] != 10000
    ):
        raise ApiError(
            409,
            "시행일·배분 기준과 합계100%인 확정 비율을 확인해주세요. DEC-01/04/07 합의가 필요합니다.",
        )
    return result


def end_of(row):
    return min((x for x in (row["expires_at"], row["stops_at"]) if x), default=None)


def binding_state(row, at):
    end = end_of(row)
    if end and end <= row["starts_at"]:
        return "cancelled"
    if row["starts_at"] > at:
        return "scheduled"
    if end and at >= end:
        return "stopped" if row["stops_at"] and end == row["stops_at"] else "expired"
    return "active"


def binding_view(row, at=None):
    at = at or now_iso()
    return {
        "id": row["id"],
        "courseId": row["course_id"],
        "contractVersionId": row["contract_version_id"],
        "mode": row["mode"],
        "startsAt": row["starts_at"],
        "expiresAt": row["expires_at"],
        "stopsAt": row["stops_at"],
        "effectiveEnd": end_of(row),
        "state": binding_state(row, at),
        "courseVersion": row["course_version"],
        "reason": row["reason"],
        "version": row["version"],
        "createdAt": row["created_at"],
    }


def readiness(c, course_id, mode, at=None):
    at = at or now_iso()
    course = c.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
    # Pilot/test checkout retains its explicitly labelled non-commercial route.
    if course and course["pilot"] and mode == "test":
        return {
            "ready": True,
            "classification": "pilot",
            "digest": "pilot",
            "binding": None,
            "terms": None,
        }
    candidates = c.execute(
        "SELECT b.*,v.payload,v.payload_hash,v.status AS contract_status,v.contract_id,v.edition,p.partner_user_id FROM product_contracts b JOIN contract_versions v ON v.id=b.contract_version_id JOIN partner_contracts p ON p.id=v.contract_id WHERE b.course_id=? AND b.mode=?",
        (course_id, mode),
    ).fetchall()
    active = [r for r in candidates if binding_state(r, at) == "active"]
    error = "상품의 판매 준비가 완료되지 않았습니다. 운영 확인 후 다시 진행해주세요."
    reason = "현재 적용 기간에 유효한 상품 계약이 없습니다."
    if len(active) == 1:
        row = active[0]
        terms = json.loads(row["payload"])
        if (
            row["contract_status"] == "recorded"
            and course
            and course["instructor_user_id"] == row["partner_user_id"]
            and qualified(c, row["partner_user_id"])
            and terms["effectiveAt"] <= at
            and (not terms["expiresAt"] or at < terms["expiresAt"])
        ):
            return {
                "ready": True,
                "classification": "versioned",
                "digest": digest(
                    [
                        row["id"],
                        row["payload_hash"],
                        row["starts_at"],
                        row["expires_at"],
                        row["stops_at"],
                    ]
                ),
                "binding": row,
                "terms": terms,
            }
        if row["contract_status"] != "recorded":
            reason = "합의 기록이 철회되었거나 아직 확인되지 않았습니다."
        elif not course or course["instructor_user_id"] != row["partner_user_id"]:
            reason = "현재 강의 담당자와 계약 파트너가 다릅니다."
        elif not qualified(c, row["partner_user_id"]):
            reason = "파트너 계정의 활성 상태와 코치 권한을 확인해주세요."
        else:
            reason = "계약 원문의 시행/종료 기간 밖입니다."
    if len(active) > 1:
        error = "상품의 적용 기간이 상충합니다. 운영 확인이 필요합니다."
        reason = error
    return {
        "ready": False,
        "classification": "unavailable",
        "message": error,
        "reason": reason,
        "digest": None,
        "binding": None,
        "terms": None,
    }


def seal_order_contract(c, order_id, at=None):
    order = c.execute(
        "SELECT * FROM purchase_orders WHERE id=?", (order_id,)
    ).fetchone()
    at = at or now_iso()
    ready = readiness(c, order["course_id"], order["mode"], at)
    if not ready["ready"]:
        raise ApiError(409, ready["message"])
    if ready["classification"] == "pilot":
        return
    row = ready["binding"]
    snapshot = {
        "contractId": row["contract_id"],
        "contractVersionId": row["contract_version_id"],
        "edition": row["edition"],
        "partnerUserId": row["partner_user_id"],
        "terms": ready["terms"],
        "termsHash": row["payload_hash"],
        "binding": binding_view(row, at),
        "courseId": order["course_id"],
        "title": order["title"],
        "amount": order["amount"],
        "currency": order["currency"],
        "mode": order["mode"],
    }
    c.execute(
        "INSERT INTO order_contract_snapshots VALUES(?,?,?,?,?,?)",
        (
            order_id,
            row["id"],
            row["contract_version_id"],
            encode(snapshot),
            digest(snapshot),
            at,
        ),
    )


def authorize_contract(c, order):
    # A verified first-start policy record is committed with the first PG attempt.
    # Later unknown-result recovery must keep the original financial identity.
    if c.execute(
        "SELECT 1 FROM payment_policy_checks WHERE order_id=?", (order["id"],)
    ).fetchone():
        return
    if c.execute(
        "SELECT 1 FROM contract_legacy_attempts WHERE order_id=?", (order["id"],)
    ).fetchone():
        return
    ready = readiness(c, order["course_id"], order["mode"])
    if ready["classification"] == "pilot" and ready["ready"]:
        return
    snapshot = c.execute(
        "SELECT * FROM order_contract_snapshots WHERE order_id=?", (order["id"],)
    ).fetchone()
    if (
        not snapshot
        or not ready["ready"]
        or snapshot["binding_id"] != ready["binding"]["id"]
    ):
        raise ApiError(
            409,
            "주문에 적용한 상품 계약을 현재 사용할 수 없습니다. 운영 확인 후 새 주문을 준비해주세요.",
        )
    payload = json.loads(snapshot["payload"])
    if (
        digest(payload) != snapshot["payload_hash"]
        or payload["termsHash"] != ready["binding"]["payload_hash"]
    ):
        raise ApiError(409, "주문의 합의 조건 원문을 확인할 수 없습니다.")


class ContractService:
    def __init__(self, app):
        self.app = app

    def event(self, c, user, cid, action, reason, vid=None, bid=None, details=None):
        c.execute(
            "INSERT INTO contract_events VALUES(?,?,?,?,?,?,?,?,?)",
            (
                new_id("contractevent"),
                cid,
                vid,
                bid,
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
            "contract." + action,
            "contract",
            cid,
            {"versionId": vid, "bindingId": bid},
        )

    def partners(self, user, query):
        page, size, offset = page_args(query)
        term = query.get("q", [""])[0].strip()[:100]
        with self.app.db.connect() as c:
            current_actor(c, user, True)
            cond = " FROM users u WHERE u.status='active' AND (u.role='admin' OR EXISTS(SELECT 1 FROM user_roles r WHERE r.user_id=u.id AND r.role='coach')) AND (u.name LIKE ? OR u.email LIKE ?)"
            args = ("%" + term + "%", "%" + term + "%")
            return {
                "users": [
                    dict(r)
                    for r in c.execute(
                        "SELECT u.id,u.name,u.email"
                        + cond
                        + " ORDER BY u.name,u.id LIMIT ? OFFSET ?",
                        args + (size, offset),
                    )
                ],
                "total": c.execute("SELECT COUNT(*)" + cond, args).fetchone()[0],
                "page": page,
                "pageSize": size,
            }

    def listing(self, user, query):
        page, size, offset = page_args(query)
        with self.app.db.connect() as c:
            user = current_actor(c, user)
            where = " WHERE (p.title LIKE ? OR u.name LIKE ?)"
            term = "%" + query.get("q", [""])[0].strip()[:100] + "%"
            args = [term, term]
            if user["role"] != "admin":
                where += " AND p.partner_user_id=?"
                args.append(user["id"])
            mode = query.get("mode", [""])[0]
            if mode in ("test", "live"):
                where += " AND p.mode=?"
                args.append(mode)
            base = (
                " FROM partner_contracts p JOIN users u ON u.id=p.partner_user_id"
                + where
            )
            rows = c.execute(
                "SELECT p.*,u.name AS partner_name"
                + base
                + " ORDER BY p.created_at DESC,p.id LIMIT ? OFFSET ?",
                args + [size, offset],
            ).fetchall()
            return {
                "contracts": [
                    {
                        **self.contract_view(r),
                        "latestEdition": c.execute(
                            "SELECT MAX(edition) FROM contract_versions WHERE contract_id=?",
                            (r["id"],),
                        ).fetchone()[0]
                        or 0,
                    }
                    for r in rows
                ],
                "total": c.execute("SELECT COUNT(*)" + base, args).fetchone()[0],
                "page": page,
                "pageSize": size,
            }

    def contract_view(self, row):
        return {
            "id": row["id"],
            "title": row["title"],
            "partnerUserId": row["partner_user_id"],
            "partnerName": row["partner_name"],
            "mode": row["mode"],
            "version": row["version"],
            "createdAt": row["created_at"],
        }

    def version_view(self, row, admin):
        result = {
            "id": row["id"],
            "contractId": row["contract_id"],
            "edition": row["edition"],
            "status": row["status"],
            "payload": json.loads(row["payload"]),
            "payloadHash": row["payload_hash"],
            "version": row["version"],
            "reason": row["reason"],
            "reviewNote": row["review_note"],
            "recordedAt": row["recorded_at"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "agreementRecorded": bool(row["agreement_reference"]),
        }
        if admin:
            result.update(
                agreementReference=row["agreement_reference"],
                decisionReferences=json.loads(row["decision_references"]),
            )
        return result

    def detail(self, user, cid, query):
        page, size, offset = page_args(query)
        with self.app.db.connect() as c:
            user, contract = managed(c, user, cid)
            rows = c.execute(
                "SELECT * FROM contract_versions WHERE contract_id=? ORDER BY edition DESC LIMIT ? OFFSET ?",
                (cid, size, offset),
            ).fetchall()
            bindings = c.execute(
                "SELECT b.*,co.title AS course_title FROM product_contracts b JOIN contract_versions v ON v.id=b.contract_version_id JOIN courses co ON co.id=b.course_id WHERE v.contract_id=? ORDER BY b.created_at DESC,b.rowid DESC LIMIT 100",
                (cid,),
            ).fetchall()
            return {
                "contract": self.contract_view(contract),
                "versions": [
                    self.version_view(r, user["role"] == "admin") for r in rows
                ],
                "page": page,
                "pageSize": size,
                "total": c.execute(
                    "SELECT COUNT(*) FROM contract_versions WHERE contract_id=?", (cid,)
                ).fetchone()[0],
                "bindings": [
                    {**binding_view(r), "courseTitle": r["course_title"]}
                    for r in bindings
                ],
                "events": [
                    {
                        "action": r["action"],
                        "reason": r["reason"],
                        "createdAt": r["created_at"],
                        "details": json.loads(r["details"]),
                    }
                    for r in c.execute(
                        "SELECT * FROM contract_events WHERE contract_id=? ORDER BY rowid DESC LIMIT 80",
                        (cid,),
                    )
                ],
            }

    def exact(self, user, cid, vid):
        with self.app.db.connect() as c:
            user, _ = managed(c, user, cid)
            row = c.execute(
                "SELECT * FROM contract_versions WHERE id=? AND contract_id=?",
                (vid, cid),
            ).fetchone()
            if not row:
                raise ApiError(404, "계약 버전을 찾을 수 없습니다.")
            return {"contractVersion": self.version_view(row, user["role"] == "admin")}

    def create(self, user, data):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = current_actor(c, user, True)
            partner = text_field(data, "partnerUserId", maximum=100)
            if not qualified(c, partner):
                raise ApiError(400, "활성 코치/관리자 파트너를 지정해주세요.")
            mode = choice_field(data, "mode", ("test", "live"))
            title = text_field(data, "title", minimum=2, maximum=150)
            reason = text_field(data, "reason", minimum=5, maximum=1000)
            cid = new_id("contract")
            at = now_iso()
            c.execute(
                "INSERT INTO partner_contracts(id,partner_user_id,mode,title,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (cid, partner, mode, title, user["id"], at, at),
            )
            self.event(c, user, cid, "created", reason)
            return {"contractId": cid}

    def new_version(self, user, cid):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, contract = managed(c, user, cid, True)
            opened = c.execute(
                "SELECT * FROM contract_versions WHERE contract_id=? AND status IN ('draft','submitted','rejected')",
                (cid,),
            ).fetchone()
            if opened:
                return {"contractVersion": self.version_view(opened, True)}
            previous = c.execute(
                "SELECT * FROM contract_versions WHERE contract_id=? ORDER BY edition DESC LIMIT 1",
                (cid,),
            ).fetchone()
            payload = (
                json.loads(previous["payload"])
                if previous
                else {
                    "title": contract["title"],
                    "productionModel": "unresolved",
                    "currency": "KRW",
                    "revenueBasis": "unresolved",
                    "coachShareBps": None,
                    "companyShareBps": None,
                    "effectiveAt": None,
                    "expiresAt": None,
                    **{k: "" for k in TEXT_FIELDS},
                }
            )
            vid = new_id("contractversion")
            at = now_iso()
            edition = previous["edition"] + 1 if previous else 1
            c.execute(
                "INSERT INTO contract_versions(id,contract_id,edition,payload,payload_hash,author_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    vid,
                    cid,
                    edition,
                    encode(payload),
                    digest(payload),
                    user["id"],
                    at,
                    at,
                ),
            )
            self.event(
                c,
                user,
                cid,
                "draft.created",
                "새 조건을 작성합니다. 기존 상품 적용은 유지됩니다.",
                vid,
            )
            return {
                "contractVersion": self.version_view(
                    c.execute(
                        "SELECT * FROM contract_versions WHERE id=?", (vid,)
                    ).fetchone(),
                    True,
                )
            }

    def change(self, user, cid, vid, data, save=False):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, contract = managed(c, user, cid, True)
            row = c.execute(
                "SELECT * FROM contract_versions WHERE id=? AND contract_id=?",
                (vid, cid),
            ).fetchone()
            if not row:
                raise ApiError(404, "계약 버전을 찾을 수 없습니다.")
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
                        "record",
                        "cancel",
                        "withdraw",
                    ),
                )
            )
            allowed = {
                "save": ("draft", "rejected"),
                "submit": ("draft", "rejected"),
                "withdraw_submission": ("submitted",),
                "reject": ("submitted",),
                "record": ("submitted",),
                "cancel": ("draft", "rejected"),
                "withdraw": ("recorded",),
            }
            if row["status"] not in allowed[action]:
                raise ApiError(409, "현재 계약 버전에서는 처리할 수 없는 작업입니다.")
            reason = text_field(
                data,
                "reason",
                minimum=5 if action != "save" else 0,
                maximum=1000,
                optional=action == "save",
            )
            at = now_iso()
            if action == "save":
                payload = normalize(data.get("payload"))
                c.execute(
                    "UPDATE contract_versions SET payload=?,payload_hash=?,reason=?,status='draft',version=version+1,updated_at=? WHERE id=?",
                    (encode(payload), digest(payload), reason, at, vid),
                )
            elif action == "submit":
                normalize(json.loads(row["payload"]), True)
                c.execute(
                    "UPDATE contract_versions SET status='submitted',reason=?,version=version+1,updated_at=? WHERE id=?",
                    (reason, at, vid),
                )
            elif action == "record":
                normalize(json.loads(row["payload"]), True)
                if not qualified(c, contract["partner_user_id"]):
                    raise ApiError(409, "활성 담당 파트너 계정을 확인해주세요.")
                reference = text_field(
                    data, "agreementReference", minimum=5, maximum=2000
                )
                refs = data.get("decisionReferences")
                if not isinstance(refs, dict) or set(refs) != {
                    "DEC-01",
                    "DEC-04",
                    "DEC-07",
                }:
                    raise ApiError(
                        409, "배분·권리·정산 기준의 합의 참조를 모두 기록해주세요."
                    )
                refs = {k: text_field(refs, k, minimum=5, maximum=2000) for k in refs}
                c.execute(
                    "UPDATE contract_versions SET status='recorded',review_note=?,agreement_reference=?,decision_references=?,recorded_by=?,recorded_at=?,version=version+1,updated_at=? WHERE id=?",
                    (reason, reference, encode(refs), user["id"], at, at, vid),
                )
                notify(
                    c,
                    contract["partner_user_id"],
                    "파트너 조건이 기록되었습니다.",
                    "등록된 합의 조건과 적용 기간을 확인해주세요.",
                    "#/studio/contracts/" + cid,
                    event_key="contract.recorded:" + vid,
                    category="operation",
                    reference_kind="contract",
                    reference_id=cid,
                    group_key="contract:" + cid,
                )
            else:
                status = {
                    "withdraw_submission": "draft",
                    "reject": "rejected",
                    "cancel": "cancelled",
                    "withdraw": "withdrawn",
                }[action]
                c.execute(
                    "UPDATE contract_versions SET status=?,review_note=?,version=version+1,updated_at=? WHERE id=?",
                    (status, reason, at, vid),
                )
                if action == "withdraw":
                    for b in c.execute(
                        "SELECT * FROM product_contracts WHERE contract_version_id=?",
                        (vid,),
                    ).fetchall():
                        if not end_of(b) or end_of(b) > at:
                            self.stop_row(c, user, cid, b, at, reason)
            self.event(c, user, cid, action, reason, vid)
            return {
                "contractVersion": self.version_view(
                    c.execute(
                        "SELECT * FROM contract_versions WHERE id=?", (vid,)
                    ).fetchone(),
                    True,
                )
            }

    def bind(self, user, cid, vid, data):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, contract = managed(c, user, cid, True)
            row = c.execute(
                "SELECT * FROM contract_versions WHERE id=? AND contract_id=?",
                (vid, cid),
            ).fetchone()
            if not row:
                raise ApiError(404, "계약 버전을 찾을 수 없습니다.")
            version(data, row)
            if row["status"] != "recorded" or not qualified(
                c, contract["partner_user_id"]
            ):
                raise ApiError(
                    409, "현재 사용할 수 있는 합의 기록과 파트너를 확인해주세요."
                )
            course_id = text_field(data, "courseId", maximum=100)
            course = c.execute(
                "SELECT * FROM courses WHERE id=?", (course_id,)
            ).fetchone()
            if (
                not course
                or course["pilot"]
                or course["status"] == "archived"
                or course["instructor_user_id"] != contract["partner_user_id"]
            ):
                raise ApiError(409, "파트너가 담당하는 정식 강의를 선택해주세요.")
            if (
                type(data.get("courseVersion")) is not int
                or data["courseVersion"] != course["version"]
            ):
                raise ApiError(409, "상품 정보가 바뀌었습니다. 다시 확인해주세요.")
            start = canonical_time(data.get("startsAt"), "상품 적용 시작")
            end = (
                canonical_time(data["expiresAt"], "상품 적용 종료")
                if data.get("expiresAt")
                else None
            )
            terms = json.loads(row["payload"])
            if (
                start < terms["effectiveAt"]
                or (end and end <= start)
                or (terms["expiresAt"] and (not end or end > terms["expiresAt"]))
            ):
                raise ApiError(
                    400, "상품 적용 기간은 계약 기간 안의 유효한 구간이어야 합니다."
                )
            for b in c.execute(
                "SELECT * FROM product_contracts WHERE course_id=? AND mode=?",
                (course_id, contract["mode"]),
            ):
                stop = end_of(b)
                if stop and stop <= b["starts_at"]:
                    continue
                if (not stop or start < stop) and (not end or b["starts_at"] < end):
                    raise ApiError(
                        409,
                        "같은 상품·환경의 적용 기간이 겹칩니다. 기존 적용 종료를 먼저 기록해주세요.",
                    )
            reason = text_field(data, "reason", minimum=5, maximum=1000)
            bid = new_id("productcontract")
            at = now_iso()
            c.execute(
                "INSERT INTO product_contracts(id,course_id,contract_version_id,mode,starts_at,expires_at,course_version,reason,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    bid,
                    course_id,
                    vid,
                    contract["mode"],
                    start,
                    end,
                    course["version"],
                    reason,
                    user["id"],
                    at,
                    at,
                ),
            )
            self.event(c, user, cid, "product.bound", reason, vid, bid)
            return {
                "binding": binding_view(
                    c.execute(
                        "SELECT * FROM product_contracts WHERE id=?", (bid,)
                    ).fetchone()
                )
            }

    def stop_row(self, c, user, cid, row, stop, reason):
        c.execute(
            "UPDATE product_contracts SET stops_at=?,version=version+1,updated_at=? WHERE id=?",
            (stop, now_iso(), row["id"]),
        )
        self.event(
            c,
            user,
            cid,
            "product.stopped",
            reason,
            row["contract_version_id"],
            row["id"],
            {"previousStop": row["stops_at"], "stopsAt": stop},
        )

    def stop(self, user, bid, data):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = current_actor(c, user, True)
            row = c.execute(
                "SELECT b.*,v.contract_id FROM product_contracts b JOIN contract_versions v ON v.id=b.contract_version_id WHERE b.id=?",
                (bid,),
            ).fetchone()
            if not row:
                raise ApiError(404, "상품 적용 기록을 찾을 수 없습니다.")
            version(data, row)
            at = now_iso()
            stop = (
                canonical_time(data["stopsAt"], "종료 예정 시각")
                if data.get("stopsAt")
                else at
            )
            if stop < at or (end_of(row) and stop >= end_of(row)):
                raise ApiError(
                    409, "종료 시각은 현재 이후이고 기존 적용 종료보다 앞서야 합니다."
                )
            reason = text_field(data, "reason", minimum=5, maximum=1000)
            self.stop_row(c, user, row["contract_id"], row, stop, reason)
            return {
                "binding": binding_view(
                    c.execute(
                        "SELECT * FROM product_contracts WHERE id=?", (bid,)
                    ).fetchone()
                )
            }

    def orders(self, user, cid, query):
        page, size, offset = page_args(query)
        with self.app.db.connect() as c:
            managed(c, user, cid)
            base = " FROM order_contract_snapshots s JOIN contract_versions v ON v.id=s.contract_version_id JOIN purchase_orders o ON o.id=s.order_id WHERE v.contract_id=?"
            return {
                "orders": [
                    {
                        "orderId": r["order_id"],
                        "contractVersionId": r["contract_version_id"],
                        "edition": r["edition"],
                        "sealedAt": r["sealed_at"],
                        "title": r["title"],
                        "amount": r["amount"],
                        "currency": r["currency"],
                        "status": r["status"],
                    }
                    for r in c.execute(
                        "SELECT s.order_id,s.contract_version_id,v.edition,s.sealed_at,o.title,o.amount,o.currency,o.status"
                        + base
                        + " ORDER BY s.sealed_at DESC,s.rowid DESC LIMIT ? OFFSET ?",
                        (cid, size, offset),
                    )
                ],
                "total": c.execute("SELECT COUNT(*)" + base, (cid,)).fetchone()[0],
                "page": page,
                "pageSize": size,
            }

    def bindings(self, user, cid, query):
        page, size, offset = page_args(query)
        with self.app.db.connect() as c:
            managed(c, user, cid)
            at = now_iso()
            base = " FROM product_contracts b JOIN contract_versions v ON v.id=b.contract_version_id JOIN courses co ON co.id=b.course_id WHERE v.contract_id=?"
            rows = c.execute(
                "SELECT b.*,v.edition,co.title AS course_title"
                + base
                + " ORDER BY b.created_at DESC,b.rowid DESC LIMIT ? OFFSET ?",
                (cid, size, offset),
            ).fetchall()
            cache = {}
            result = []
            for row in rows:
                key = (row["course_id"], row["mode"])
                if key not in cache:
                    cache[key] = readiness(c, *key, at)
                ready = cache[key]
                selected = bool(
                    ready["ready"]
                    and ready["binding"]
                    and ready["binding"]["id"] == row["id"]
                )
                result.append(
                    {
                        **binding_view(row, at),
                        "edition": row["edition"],
                        "courseTitle": row["course_title"],
                        "currentApplication": selected,
                        "readinessReason": (
                            "현재 상품 제공 조건에 연결되어 있습니다."
                            if selected
                            else ready.get("reason", "현재 적용 버전이 아닙니다.")
                        ),
                    }
                )
            return {
                "bindings": result,
                "total": c.execute("SELECT COUNT(*)" + base, (cid,)).fetchone()[0],
                "page": page,
                "pageSize": size,
            }

    def order_snapshot(self, user, order_id, admin=False):
        with self.app.db.connect() as c:
            user = current_actor(c, user, admin)
            row = c.execute(
                "SELECT s.*,p.partner_user_id FROM order_contract_snapshots s JOIN contract_versions v ON v.id=s.contract_version_id JOIN partner_contracts p ON p.id=v.contract_id WHERE s.order_id=?",
                (order_id,),
            ).fetchone()
            if row:
                if user["role"] != "admin" and row["partner_user_id"] != user["id"]:
                    raise ApiError(404, "조회할 수 있는 거래 기록을 찾을 수 없습니다.")
                return {
                    "contractSnapshot": {
                        "recorded": True,
                        "orderId": order_id,
                        "sealedAt": row["sealed_at"],
                        "payload": json.loads(row["payload"]),
                        "payloadHash": row["payload_hash"],
                    }
                }
            if (
                user["role"] != "admin"
                or not c.execute(
                    "SELECT 1 FROM purchase_orders WHERE id=?", (order_id,)
                ).fetchone()
            ):
                raise ApiError(404, "조회할 수 있는 거래 기록을 찾을 수 없습니다.")
            return {"contractSnapshot": {"recorded": False, "orderId": order_id}}
