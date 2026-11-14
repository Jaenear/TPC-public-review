"""Reviewed coach profiles, scoped public facts and durable inquiry handoffs.

Content review is distinct from a claim's verification. No real coach, rights
approval, performance record or endorsement is seeded by this service.
"""

import hashlib
import json
import re
from urllib.parse import urlsplit, parse_qs

from ..access import canonical_time
from ..core import ApiError, choice_field, text_field, new_id, now_iso
from .common import audit, notify, require_role, roles, version, row_dict, page_args


def encode(payload):
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def payload_hash(payload):
    return hashlib.sha256(encode(payload).encode()).hexdigest()


def actor(c, user):
    row = (
        c.execute(
            "SELECT * FROM users WHERE id=? AND status='active'", (user["id"],)
        ).fetchone()
        if user
        else None
    )
    if not row:
        raise ApiError(401, "로그인 상태를 다시 확인해주세요.")
    require_role(c, row, "coach")
    return row


def owner_eligible(c, owner_id):
    return bool(
        owner_id
        and c.execute(
            "SELECT 1 FROM users u WHERE u.id=? AND u.status='active' AND (u.role='admin' OR EXISTS(SELECT 1 FROM user_roles r WHERE r.user_id=u.id AND r.role='coach'))",
            (owner_id,),
        ).fetchone()
    )


def available(c, profile):
    return bool(
        profile
        and profile["state"] == "active"
        and profile["public_revision_id"]
        and (
            profile["owner_user_id"] is None
            or owner_eligible(c, profile["owner_user_id"])
        )
    )


def managed(c, user, coach_id):
    user = actor(c, user)
    profile = c.execute(
        "SELECT * FROM coach_profiles WHERE id=?", (coach_id,)
    ).fetchone()
    if not profile:
        raise ApiError(404, "코치 프로필을 찾을 수 없습니다.")
    if user["role"] != "admin" and profile["owner_user_id"] != user["id"]:
        raise ApiError(403, "담당 프로필만 관리할 수 있습니다.")
    return user, profile


def published_payload(c, profile):
    row = (
        c.execute(
            "SELECT * FROM coach_profile_revisions WHERE id=? AND status='approved'",
            (profile["public_revision_id"],),
        ).fetchone()
        if profile and profile["public_revision_id"]
        else None
    )
    return row, json.loads(row["payload"]) if row else None


def video_url(value):
    if not isinstance(value, str) or len(value) > 500:
        raise ApiError(400, "YouTube 샘플 주소를 확인해주세요.")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ApiError(400, "샘플은 HTTPS YouTube 주소로 입력해주세요.")
    video = (
        parsed.path.lstrip("/")
        if host == "youtu.be"
        else (
            parse_qs(parsed.query).get("v", [""])[0]
            if host in ("youtube.com", "www.youtube.com") and parsed.path == "/watch"
            else ""
        )
    )
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
        raise ApiError(400, "유효한 YouTube 영상 주소를 입력해주세요.")
    return "https://www.youtube.com/watch?v=" + video


def samples(c, entries, *, strict):
    if not isinstance(entries, list) or len(entries) > 3:
        raise ApiError(400, "샘플은 최대3개까지 등록할 수 있습니다.")
    result = []
    for sample in entries:
        if not isinstance(sample, dict):
            raise ApiError(400, "샘플 구성을 확인해주세요.")
        title = text_field(sample, "title", minimum=2, maximum=100)
        kind = choice_field(sample, "kind", ("lesson", "video"))
        if kind == "video":
            try:
                link = video_url(sample.get("url"))
            except ValueError:
                raise ApiError(400, "샘플 주소를 확인해주세요.")
            result.append({"kind": kind, "title": title, "url": link})
        else:
            course = text_field(sample, "courseId", maximum=100)
            lesson = text_field(sample, "lessonId", maximum=100)
            valid = c.execute(
                "SELECT 1 FROM courses c JOIN course_lessons l ON l.course_id=c.id WHERE c.id=? AND l.id=? AND c.status='published' AND l.status='published' AND l.free=1",
                (course, lesson),
            ).fetchone()
            if valid:
                result.append(
                    {
                        "kind": kind,
                        "title": title,
                        "courseId": course,
                        "lessonId": lesson,
                    }
                )
            elif strict:
                raise ApiError(
                    400, "차시 샘플은 현재 무료 공개 중인 차시만 연결할 수 있습니다."
                )
    return result


def string_list(data, key, minimum, maximum, length):
    value = data.get(key)
    if (
        not isinstance(value, list)
        or not minimum <= len(value) <= maximum
        or any(
            not isinstance(x, str) or not 1 <= len(x.strip()) <= length for x in value
        )
    ):
        raise ApiError(400, key + " 항목의 개수와 길이를 확인해주세요.")
    value = [x.strip() for x in value]
    if len(set(value)) != len(value):
        raise ApiError(400, "중복 항목을 제거해주세요.")
    return value


def normalized_payload(c, data):
    if not isinstance(data, dict):
        raise ApiError(400, "프로필 내용을 확인해주세요.")
    result = {
        key: text_field(data, key, minimum=low, maximum=high)
        for key, low, high in (
            ("name", 2, 60),
            ("role", 3, 100),
            ("bio", 20, 4000),
            ("teachingApproach", 20, 3000),
            ("initials", 1, 4),
        )
    }
    if not re.fullmatch(r"[A-Za-z0-9]{1,4}", result["initials"]):
        raise ApiError(400, "이니셜은 영문/숫자1~4자로 입력해주세요.")
    result["accent"] = choice_field(data, "accent", ("green", "gold", "blue"))
    result["specialty"] = string_list(data, "specialty", 1, 8, 40)
    result["levels"] = string_list(data, "levels", 1, 3, 10)
    if set(result["levels"]) - {"입문", "중급", "심화"}:
        raise ApiError(400, "대상 난이도를 확인해주세요.")
    result["claims"] = string_list(data, "claims", 0, 5, 300)
    for key in ("pilot", "acceptsInquiry"):
        if type(data.get(key)) is not bool:
            raise ApiError(400, "프로필 표시/접수 여부를 확인해주세요.")
        result[key] = data[key]
    result["samples"] = samples(c, data.get("samples", []), strict=True)
    return result


def verification_view(row, profile, at, admin=False):
    state = (
        "revoked"
        if row["revoked_at"]
        else (
            "expired"
            if row["expires_at"] and row["expires_at"] <= at
            else (
                "superseded"
                if row["revision_id"] != profile["public_revision_id"]
                else "active"
            )
        )
    )
    result = {
        "id": row["id"],
        "kind": row["kind"],
        "label": row["label"],
        "publicNote": row["public_note"],
        "checkedAt": row["checked_at"],
        "expiresAt": row["expires_at"],
        "status": state,
        "revisionId": row["revision_id"],
        "version": row["version"],
    }
    if admin:
        result.update(
            approvalReference=row["approval_reference"],
            revokedAt=row["revoked_at"],
            revocationReason=row["revocation_reason"],
        )
    return result


def public_profile(c, profile, at=None):
    if not available(c, profile):
        return None
    revision, payload = published_payload(c, profile)
    if payload is None:
        return None
    at = at or now_iso()
    facts = (
        []
        if payload["pilot"]
        else [
            verification_view(r, profile, at)
            for r in c.execute(
                "SELECT * FROM coach_verifications WHERE coach_id=? AND revision_id=? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?) ORDER BY checked_at,id",
                (profile["id"], revision["id"], at),
            )
        ]
    )
    courses = [
        r[0]
        for r in c.execute(
            "SELECT id FROM courses WHERE status='published' AND "
            + (
                "instructor_user_id=?"
                if profile["owner_user_id"]
                else "coach_id=? AND instructor_user_id IS NULL"
            )
            + " ORDER BY created_at,id",
            (profile["owner_user_id"] or profile["id"],),
        )
    ]
    return {
        "id": profile["id"],
        **payload,
        "samples": samples(c, payload["samples"], strict=False),
        "status": "파일럿 프로필" if payload["pilot"] else "소개 검수 완료",
        "publicVersion": profile["public_version"],
        "revisionId": revision["id"],
        "publishedAt": revision["published_at"],
        "reviewedAt": revision["reviewed_at"],
        "verifiedFacts": facts,
        "courseIds": courses,
    }


def public_coaches(c):
    order = {"live-coach": 0, "mtt-coach": 1, "strategy-coach": 2}
    profiles = [
        p
        for r in c.execute(
            "SELECT * FROM coach_profiles ORDER BY created_at,id"
        ).fetchall()
        for p in [public_profile(c, r)]
        if p
    ]
    return sorted(profiles, key=lambda p: order.get(p["id"], 3))


def require_inquiry(c, coach_id):
    profile = c.execute(
        "SELECT * FROM coach_profiles WHERE id=?", (coach_id,)
    ).fetchone()
    if profile is None:
        raise ApiError(404, "코치 프로필을 찾을 수 없습니다.")
    view = public_profile(c, profile)
    if not view or not view["acceptsInquiry"]:
        raise ApiError(409, "현재 신규 코칭 접수를 받지 않는 코치입니다.")
    return profile, view


def record_booking_profile(c, booking_id, profile, view):
    at = now_iso()
    c.execute(
        "INSERT INTO booking_coach_snapshots VALUES(?,?,?,?,?,?)",
        (
            booking_id,
            profile["id"],
            view["revisionId"],
            view["publicVersion"],
            view["name"],
            at,
        ),
    )
    c.execute(
        "INSERT INTO booking_assignments VALUES(?,?,?,1,?)",
        (booking_id, profile["id"], view["revisionId"], at),
    )


def booking_routing(c, booking):
    assignment = c.execute(
        "SELECT * FROM booking_assignments WHERE booking_id=?", (booking["id"],)
    ).fetchone()
    snapshot = c.execute(
        "SELECT * FROM booking_coach_snapshots WHERE booking_id=?", (booking["id"],)
    ).fetchone()
    coach_id = assignment["coach_id"] if assignment else booking["coach_id"]
    profile = c.execute(
        "SELECT * FROM coach_profiles WHERE id=?", (coach_id,)
    ).fetchone()
    revision = (
        c.execute(
            "SELECT payload FROM coach_profile_revisions WHERE id=?",
            (assignment["revision_id"],),
        ).fetchone()
        if assignment
        else None
    )
    _, current = published_payload(c, profile)
    name = (
        json.loads(revision["payload"])["name"]
        if revision
        else current["name"] if current else coach_id
    )
    return {
        "originalCoachName": snapshot["coach_name"] if snapshot else None,
        "profileSnapshotRecorded": bool(snapshot),
        "originalProfileVersion": snapshot["public_version"] if snapshot else None,
        "assignedCoachId": coach_id,
        "assignedCoachName": name,
        "assignmentVersion": assignment["version"] if assignment else 0,
        "needsHandoff": booking["status"] not in ("완료", "취소")
        and not available(c, profile),
    }


def inquiry_view(c, row):
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "coachId": row["coach_id"],
        "type": row["type"],
        "preferredDate": row["preferred_date"],
        "preferredTime": row["preferred_time"],
        "goal": row["goal"],
        "status": row["status"],
        "reply": row["reply"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        **booking_routing(c, row),
    }


class ProfileService:
    def __init__(self, app):
        self.app = app

    def public(self, coach_id):
        with self.app.db.connect() as c:
            row = c.execute(
                "SELECT * FROM coach_profiles WHERE id=?", (coach_id,)
            ).fetchone()
            view = public_profile(c, row)
            if not view:
                raise ApiError(404, "공개 중인 코치 프로필을 찾을 수 없습니다.")
            return {"coach": view}

    def profile_view(self, c, row, admin=False):
        revision, payload = published_payload(c, row)
        owner = (
            c.execute(
                "SELECT name FROM users WHERE id=?", (row["owner_user_id"],)
            ).fetchone()
            if row["owner_user_id"]
            else None
        )
        return {
            "id": row["id"],
            "ownerUserId": row["owner_user_id"],
            "ownerName": owner["name"] if owner else None,
            "ownerEligible": owner_eligible(c, row["owner_user_id"]),
            "state": row["state"],
            "version": row["version"],
            "publicVersion": row["public_version"],
            "publicRevisionId": row["public_revision_id"],
            "publishedPayload": payload,
            "publishedAt": revision["published_at"] if revision else None,
            "publiclyAvailable": available(c, row),
            "createdAt": row["created_at"],
        }

    def owners(self, user, query):
        page, limit, offset = page_args(query)
        term = query.get("q", [""])[0].strip()
        if len(term) > 100:
            raise ApiError(400, "담당 코치 검색어는100자 이내로 입력해주세요.")
        current = query.get("coachId", [""])[0]
        with self.app.db.connect() as c:
            user = actor(c, user)
            require_role(c, user, "admin")
            where = " FROM users u LEFT JOIN coach_profiles p ON p.owner_user_id=u.id WHERE u.status='active' AND (u.role='admin' OR EXISTS(SELECT 1 FROM user_roles r WHERE r.user_id=u.id AND r.role='coach')) AND (p.id IS NULL OR p.id=?) AND (u.name LIKE ? OR u.email LIKE ?)"
            args = (current, "%" + term + "%", "%" + term + "%")
            rows = c.execute(
                "SELECT u.id,u.name,u.email"
                + where
                + " ORDER BY u.name,u.id LIMIT ? OFFSET ?",
                args + (limit, offset),
            ).fetchall()
            return {
                "users": [dict(r) for r in rows],
                "page": page,
                "pageSize": limit,
                "total": c.execute("SELECT COUNT(*)" + where, args).fetchone()[0],
            }

    def listing(self, user, query):
        with self.app.db.connect() as c:
            user = actor(c, user)
            rows = c.execute(
                "SELECT * FROM coach_profiles"
                + ("" if user["role"] == "admin" else " WHERE owner_user_id=?")
                + " ORDER BY updated_at DESC,id",
                () if user["role"] == "admin" else (user["id"],),
            ).fetchall()
            result = []
            for row in rows:
                view = self.profile_view(c, row)
                current = c.execute(
                    "SELECT id,status FROM coach_profile_revisions WHERE coach_id=? AND status IN ('draft','submitted','rejected')",
                    (row["id"],),
                ).fetchone()
                view["openRevision"] = (
                    {"id": current["id"], "status": current["status"]}
                    if current
                    else None
                )
                view["inquiryCount"] = c.execute(
                    "SELECT COUNT(*) FROM bookings b LEFT JOIN booking_assignments a ON a.booking_id=b.id WHERE COALESCE(a.coach_id,b.coach_id)=? AND b.status NOT IN ('완료','취소')",
                    (row["id"],),
                ).fetchone()[0]
                result.append(view)
            return {"profiles": result}

    def detail(self, user, coach_id):
        with self.app.db.connect() as c:
            user, profile = managed(c, user, coach_id)
            admin = user["role"] == "admin"
            revisions = c.execute(
                "SELECT * FROM coach_profile_revisions WHERE coach_id=? ORDER BY created_at DESC,rowid DESC LIMIT 40",
                (coach_id,),
            ).fetchall()
            events = c.execute(
                "SELECT * FROM coach_profile_events WHERE coach_id=? ORDER BY rowid DESC LIMIT 80",
                (coach_id,),
            ).fetchall()
            facts = c.execute(
                "SELECT * FROM coach_verifications WHERE coach_id=? ORDER BY rowid DESC LIMIT 80",
                (coach_id,),
            ).fetchall()
            return {
                "profile": self.profile_view(c, profile),
                "revisions": [self.revision_view(r, admin) for r in revisions],
                "events": [row_dict(r) for r in events],
                "verifications": [
                    verification_view(r, profile, now_iso(), admin) for r in facts
                ],
            }

    def revision_view(self, row, admin):
        result = {
            "id": row["id"],
            "coachId": row["coach_id"],
            "status": row["status"],
            "payload": json.loads(row["payload"]),
            "payloadHash": row["payload_hash"],
            "basePublicVersion": row["base_public_version"],
            "baseProfileVersion": row["base_profile_version"],
            "reason": row["reason"],
            "reviewNote": row["review_note"],
            "version": row["version"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "submittedAt": row["submitted_at"],
            "reviewedAt": row["reviewed_at"],
            "publishedAt": row["published_at"],
        }
        if admin:
            result["approvalReference"] = row["approval_reference"]
        return result

    def history(self, user, coach_id, query):
        page, limit, offset = page_args(query)
        with self.app.db.connect() as c:
            user, _ = managed(c, user, coach_id)
            rows = c.execute(
                "SELECT * FROM coach_profile_revisions WHERE coach_id=? ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?",
                (coach_id, limit, offset),
            ).fetchall()
            return {
                "revisions": [
                    self.revision_view(r, user["role"] == "admin") for r in rows
                ],
                "page": page,
                "pageSize": limit,
                "total": c.execute(
                    "SELECT COUNT(*) FROM coach_profile_revisions WHERE coach_id=?",
                    (coach_id,),
                ).fetchone()[0],
            }

    def get_revision(self, user, coach_id, rid):
        with self.app.db.connect() as c:
            user, _ = managed(c, user, coach_id)
            row = c.execute(
                "SELECT * FROM coach_profile_revisions WHERE coach_id=? AND id=?",
                (coach_id, rid),
            ).fetchone()
            if not row:
                raise ApiError(404, "프로필 변경안을 찾을 수 없습니다.")
            return {"revision": self.revision_view(row, user["role"] == "admin")}

    def event(self, c, user, profile, revision, action, note):
        c.execute(
            "INSERT INTO coach_profile_events VALUES(?,?,?,?,?,?,?,?)",
            (
                new_id("profileevent"),
                profile["id"],
                revision["id"] if revision else None,
                user["id"],
                action,
                note,
                revision["version"] if revision else None,
                now_iso(),
            ),
        )
        audit(
            c,
            user,
            "profile." + action,
            "coach",
            profile["id"],
            {"revisionId": revision["id"] if revision else None, "reason": note},
        )

    def create(self, user, data):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = actor(c, user)
            require_role(c, user, "admin")
            owner = text_field(data, "ownerUserId", maximum=100, optional=True) or None
            if owner and not owner_eligible(c, owner):
                raise ApiError(400, "활성 코치 계정을 지정해주세요.")
            if (
                owner
                and c.execute(
                    "SELECT 1 FROM coach_profiles WHERE owner_user_id=?", (owner,)
                ).fetchone()
            ):
                raise ApiError(409, "이미 프로필에 연결된 코치 계정입니다.")
            reason = text_field(data, "reason", minimum=5, maximum=1000)
            key = new_id("coach").replace("_", "-")
            at = now_iso()
            c.execute(
                "INSERT INTO coach_profiles(id,owner_user_id,created_at,updated_at) VALUES(?,?,?,?)",
                (key, owner, at, at),
            )
            profile = c.execute(
                "SELECT * FROM coach_profiles WHERE id=?", (key,)
            ).fetchone()
            self.event(c, user, profile, None, "created", reason)
            return {"profile": self.profile_view(c, profile)}

    def manage(self, user, coach_id, data):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, profile = managed(c, user, coach_id)
            require_role(c, user, "admin")
            version(data, profile)
            reason = text_field(data, "reason", minimum=5, maximum=1000)
            state = data.get("state", profile["state"])
            if state not in ("active", "inactive"):
                raise ApiError(400, "프로필 운영 상태를 확인해주세요.")
            owner = data.get("ownerUserId", profile["owner_user_id"])
            if owner is not None and not isinstance(owner, str):
                raise ApiError(400, "담당 계정을 확인해주세요.")
            owner = owner or None
            if (
                owner
                and owner != profile["owner_user_id"]
                and not owner_eligible(c, owner)
            ):
                raise ApiError(400, "활성 코치 계정을 지정해주세요.")
            if owner != profile["owner_user_id"]:
                if (
                    owner
                    and c.execute(
                        "SELECT 1 FROM coach_profiles WHERE owner_user_id=? AND id!=?",
                        (owner, coach_id),
                    ).fetchone()
                ):
                    raise ApiError(409, "이미 다른 프로필에 연결된 코치 계정입니다.")
                if profile["owner_user_id"] and (
                    c.execute(
                        "SELECT 1 FROM coach_profile_revisions WHERE coach_id=? AND status='approved' AND json_extract(payload,'$.pilot')=0",
                        (coach_id,),
                    ).fetchone()
                    or c.execute(
                        "SELECT 1 FROM bookings b LEFT JOIN booking_assignments a ON a.booking_id=b.id WHERE b.coach_id=? OR a.coach_id=? LIMIT 1",
                        (coach_id, coach_id),
                    ).fetchone()
                ):
                    raise ApiError(
                        409,
                        "공개 실재 프로필/신청 이력이 있는 코치의 계정은 바꿀 수 없습니다. 새 프로필을 만들고 기존 접수를 인계해주세요.",
                    )
            c.execute(
                "UPDATE coach_profiles SET owner_user_id=?,state=?,version=version+1,updated_at=? WHERE id=?",
                (owner, state, now_iso(), coach_id),
            )
            self.event(c, user, profile, None, "managed", reason)
            return {
                "profile": self.profile_view(
                    c,
                    c.execute(
                        "SELECT * FROM coach_profiles WHERE id=?", (coach_id,)
                    ).fetchone(),
                )
            }

    def new_revision(self, user, coach_id, data):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, profile = managed(c, user, coach_id)
            old = c.execute(
                "SELECT * FROM coach_profile_revisions WHERE coach_id=? AND status IN ('draft','submitted','rejected')",
                (coach_id,),
            ).fetchone()
            if old:
                return {"revision": self.revision_view(old, user["role"] == "admin")}
            _, payload = published_payload(c, profile)
            if payload is None:
                payload = {
                    "name": "",
                    "role": "",
                    "bio": "",
                    "initials": "TPC",
                    "accent": "green",
                    "specialty": [],
                    "levels": ["입문"],
                    "teachingApproach": "",
                    "claims": [],
                    "samples": [],
                    "pilot": True,
                    "acceptsInquiry": False,
                }
            rid = new_id("profilerevision")
            at = now_iso()
            c.execute(
                "INSERT INTO coach_profile_revisions(id,coach_id,author_id,base_public_version,base_profile_version,payload,payload_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    rid,
                    coach_id,
                    user["id"],
                    profile["public_version"],
                    profile["version"],
                    encode(payload),
                    payload_hash(payload),
                    at,
                    at,
                ),
            )
            revision = c.execute(
                "SELECT * FROM coach_profile_revisions WHERE id=?", (rid,)
            ).fetchone()
            self.event(
                c,
                user,
                profile,
                revision,
                "draft.created",
                "공개 중인 소개를 유지하고 변경안을 만듭니다.",
            )
            return {"revision": self.revision_view(revision, user["role"] == "admin")}

    def change_revision(self, user, coach_id, rid, data, save=False):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, profile = managed(c, user, coach_id)
            row = c.execute(
                "SELECT * FROM coach_profile_revisions WHERE id=? AND coach_id=?",
                (rid, coach_id),
            ).fetchone()
            if not row:
                raise ApiError(404, "프로필 변경안을 찾을 수 없습니다.")
            version(data, row)
            at = now_iso()
            action = (
                "saved"
                if save
                else choice_field(
                    data,
                    "action",
                    ("submit", "withdraw", "approve", "reject", "cancel", "rebase"),
                )
            )
            if action in ("approve", "reject"):
                require_role(c, user, "admin")
            if action in ("saved", "submit", "rebase") and row["status"] not in (
                "draft",
                "rejected",
            ):
                raise ApiError(409, "초안이나 반려된 변경안만 저장/제출할 수 있습니다.")
            if (
                action in ("approve", "reject", "withdraw")
                and row["status"] != "submitted"
            ):
                raise ApiError(409, "제출된 변경안에서만 처리할 수 있습니다.")
            if action == "cancel" and row["status"] not in ("draft", "rejected"):
                raise ApiError(409, "초안/반려 변경안만 중단할 수 있습니다.")
            stale = (
                row["base_public_version"] != profile["public_version"]
                or row["base_profile_version"] != profile["version"]
            )
            if stale and action in ("saved", "submit", "approve"):
                raise ApiError(
                    409,
                    "기준 프로필이 바뀌었습니다. 공개본과 운영 상태를 비교하고 기준을 다시 연결해주세요.",
                )
            note = (
                text_field(data, "reason", minimum=5, maximum=1000)
                if action not in ("saved",)
                else text_field(data, "reason", maximum=1000, optional=True)
            )
            if action == "saved":
                payload = normalized_payload(c, data.get("payload"))
                c.execute(
                    "UPDATE coach_profile_revisions SET payload=?,payload_hash=?,reason=?,status='draft',version=version+1,updated_at=? WHERE id=?",
                    (encode(payload), payload_hash(payload), note, at, rid),
                )
            elif action == "rebase":
                c.execute(
                    "UPDATE coach_profile_revisions SET base_public_version=?,base_profile_version=?,status='draft',version=version+1,updated_at=? WHERE id=?",
                    (profile["public_version"], profile["version"], at, rid),
                )
            elif action == "submit":
                normalized_payload(c, json.loads(row["payload"]))
                c.execute(
                    "UPDATE coach_profile_revisions SET status='submitted',reason=?,submitted_at=?,version=version+1,updated_at=? WHERE id=?",
                    (note, at, at, rid),
                )
            elif action == "approve":
                payload = normalized_payload(c, json.loads(row["payload"]))
                if profile["state"] != "active" or (
                    not payload["pilot"]
                    and not owner_eligible(c, profile["owner_user_id"])
                ):
                    raise ApiError(
                        409, "활성 프로필과 실재 코치의 활성 담당 계정을 확인해주세요."
                    )
                reference = text_field(
                    data, "approvalReference", minimum=5, maximum=2000
                )
                c.execute(
                    "UPDATE coach_profile_revisions SET status='approved',review_note=?,approval_reference=?,reviewer_id=?,reviewed_at=?,published_at=?,version=version+1,updated_at=? WHERE id=?",
                    (note, reference, user["id"], at, at, at, rid),
                )
                c.execute(
                    "UPDATE coach_profiles SET public_revision_id=?,public_version=public_version+1,version=version+1,updated_at=? WHERE id=?",
                    (rid, at, coach_id),
                )
            elif action == "reject":
                c.execute(
                    "UPDATE coach_profile_revisions SET status='rejected',review_note=?,reviewer_id=?,reviewed_at=?,version=version+1,updated_at=? WHERE id=?",
                    (note, user["id"], at, at, rid),
                )
            else:
                c.execute(
                    "UPDATE coach_profile_revisions SET status=?,version=version+1,updated_at=? WHERE id=?",
                    ("draft" if action == "withdraw" else "cancelled", at, rid),
                )
            result = c.execute(
                "SELECT * FROM coach_profile_revisions WHERE id=?", (rid,)
            ).fetchone()
            self.event(c, user, profile, result, action, note)
            return {"revision": self.revision_view(result, user["role"] == "admin")}

    def verification(self, user, coach_id, data, fact_id=None):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, profile = managed(c, user, coach_id)
            require_role(c, user, "admin")
            at = now_iso()
            if fact_id:
                row = c.execute(
                    "SELECT * FROM coach_verifications WHERE id=? AND coach_id=?",
                    (fact_id, coach_id),
                ).fetchone()
                if not row:
                    raise ApiError(404, "확인 사실을 찾을 수 없습니다.")
                version(data, row)
                if row["revoked_at"]:
                    raise ApiError(409, "이미 회수한 확인 사실입니다.")
                note = text_field(data, "reason", minimum=5, maximum=1000)
                c.execute(
                    "UPDATE coach_verifications SET revoked_at=?,revocation_reason=?,version=version+1 WHERE id=?",
                    (at, note, fact_id),
                )
                self.event(c, user, profile, None, "verification.revoked", note)
            else:
                if (
                    data.get("publicVersion") != profile["public_version"]
                    or type(data.get("publicVersion")) is not int
                ):
                    raise ApiError(409, "최신 공개판을 다시 확인해주세요.")
                _, payload = published_payload(c, profile)
                if not available(c, profile) or payload["pilot"]:
                    raise ApiError(
                        409,
                        "실재 코치의 활성 공개판에만 확인 사실을 연결할 수 있습니다.",
                    )
                label = text_field(data, "label", minimum=3, maximum=100)
                note = text_field(data, "publicNote", minimum=5, maximum=1000)
                reference = text_field(
                    data, "approvalReference", minimum=5, maximum=2000
                )
                kind = choice_field(
                    data, "kind", ("identity", "experience", "teaching")
                )
                end = (
                    canonical_time(data["expiresAt"], "확인 만료 시각")
                    if data.get("expiresAt")
                    else None
                )
                if end and end <= at:
                    raise ApiError(400, "확인 만료 시각은 현재보다 뒤여야 합니다.")
                if c.execute(
                    "SELECT 1 FROM coach_verifications WHERE coach_id=? AND revision_id=? AND kind=? AND label=? AND revoked_at IS NULL",
                    (coach_id, profile["public_revision_id"], kind, label),
                ).fetchone():
                    raise ApiError(409, "같은 공개판의 동일 확인 사실이 이미 있습니다.")
                fact_id = new_id("coachfact")
                c.execute(
                    "INSERT INTO coach_verifications(id,coach_id,revision_id,kind,label,public_note,approval_reference,checked_by,checked_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        fact_id,
                        coach_id,
                        profile["public_revision_id"],
                        kind,
                        label,
                        note,
                        reference,
                        user["id"],
                        at,
                        end,
                    ),
                )
                self.event(c, user, profile, None, "verification.added", label)
            row = c.execute(
                "SELECT * FROM coach_verifications WHERE id=?", (fact_id,)
            ).fetchone()
            return {"verification": verification_view(row, profile, at, True)}

    def inquiries(self, user, coach_id, query):
        page, limit, offset = page_args(query)
        with self.app.db.connect() as c:
            managed(c, user, coach_id)
            condition = " FROM bookings b LEFT JOIN booking_assignments a ON a.booking_id=b.id JOIN users u ON u.id=b.user_id WHERE COALESCE(a.coach_id,b.coach_id)=?"
            rows = c.execute(
                "SELECT b.*,u.name AS student_name"
                + condition
                + " ORDER BY b.created_at DESC,b.id DESC LIMIT ? OFFSET ?",
                (coach_id, limit, offset),
            ).fetchall()
            return {
                "bookings": [
                    {
                        **inquiry_view(c, r),
                        "studentName": r["student_name"],
                    }
                    for r in rows
                ],
                "total": c.execute(
                    "SELECT COUNT(*)" + condition, (coach_id,)
                ).fetchone()[0],
                "page": page,
                "pageSize": limit,
            }

    def handoff(self, user, booking_id, data):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = actor(c, user)
            require_role(c, user, "admin")
            booking = c.execute(
                "SELECT * FROM bookings WHERE id=?", (booking_id,)
            ).fetchone()
            if not booking:
                raise ApiError(404, "코칭 접수를 찾을 수 없습니다.")
            before = booking_routing(c, booking)
            if (
                type(data.get("assignmentVersion")) is not int
                or data["assignmentVersion"] != before["assignmentVersion"]
            ):
                raise ApiError(
                    409, "현재 담당이 바뀌었습니다. 최신 접수를 다시 확인해주세요."
                )
            if booking["status"] in ("완료", "취소"):
                raise ApiError(409, "종료된 접수는 인계할 수 없습니다.")
            target = text_field(data, "coachId", maximum=100)
            profile, view = require_inquiry(c, target)
            if target == before["assignedCoachId"]:
                raise ApiError(409, "현재 담당과 다른 코치를 선택해주세요.")
            reason = text_field(data, "reason", minimum=5, maximum=1000)
            at = now_iso()
            sequence = before["assignmentVersion"] + 1
            c.execute(
                "INSERT INTO booking_assignments VALUES(?,?,?,?,?) ON CONFLICT(booking_id) DO UPDATE SET coach_id=excluded.coach_id,revision_id=excluded.revision_id,version=excluded.version,updated_at=excluded.updated_at",
                (booking_id, target, view["revisionId"], sequence, at),
            )
            c.execute(
                "INSERT INTO booking_handoffs VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    new_id("handoff"),
                    booking_id,
                    before["assignedCoachId"],
                    target,
                    view["revisionId"],
                    user["id"],
                    reason,
                    sequence,
                    at,
                ),
            )
            audit(
                c,
                user,
                "booking.handoff",
                "booking",
                booking_id,
                {
                    "fromCoachId": before["assignedCoachId"],
                    "toCoachId": target,
                    "reason": reason,
                },
            )
            notify(
                c,
                booking["user_id"],
                "코칭 접수의 담당자가 변경되었습니다",
                view["name"] + " · " + reason,
                "#/my?tab=bookings",
                event_key="booking.handoff:" + booking_id + ":" + str(sequence),
                category="operation",
                reference_kind="booking",
                reference_id=booking_id,
                group_key="booking:" + booking_id,
            )
            return {"booking": inquiry_view(c, booking)}

    def handoff_history(self, user, booking_id):
        with self.app.db.connect() as c:
            user = actor(c, user)
            require_role(c, user, "admin")
            if not c.execute(
                "SELECT 1 FROM bookings WHERE id=?", (booking_id,)
            ).fetchone():
                raise ApiError(404, "코칭 접수를 찾을 수 없습니다.")
            rows = c.execute(
                "SELECT * FROM booking_handoffs WHERE booking_id=? ORDER BY version DESC",
                (booking_id,),
            ).fetchall()
            return {"handoffs": [row_dict(r) for r in rows]}

    def booking_snapshot(self, user, booking_id):
        with self.app.db.connect() as c:
            current = (
                c.execute(
                    "SELECT id,role FROM users WHERE id=? AND status='active'",
                    (user["id"],),
                ).fetchone()
                if user
                else None
            )
            if not current:
                raise ApiError(401, "로그인 후 신청 기록을 확인해주세요.")
            booking = c.execute(
                "SELECT * FROM bookings WHERE id=? AND (user_id=? OR ?='admin')",
                (booking_id, current["id"], current["role"]),
            ).fetchone()
            if not booking:
                raise ApiError(404, "조회할 수 있는 코칭 신청을 찾을 수 없습니다.")
            snapshot = c.execute(
                "SELECT * FROM booking_coach_snapshots WHERE booking_id=?",
                (booking_id,),
            ).fetchone()
            if not snapshot:
                return {"profileSnapshot": {"recorded": False, "bookingId": booking_id}}
            revision = c.execute(
                "SELECT payload,payload_hash FROM coach_profile_revisions WHERE id=?",
                (snapshot["revision_id"],),
            ).fetchone()
            return {
                "profileSnapshot": {
                    "recorded": True,
                    "bookingId": booking_id,
                    "coachId": snapshot["coach_id"],
                    "revisionId": snapshot["revision_id"],
                    "publicVersion": snapshot["public_version"],
                    "recordedAt": snapshot["recorded_at"],
                    "payload": json.loads(revision["payload"]),
                    "payloadHash": revision["payload_hash"],
                    "verificationSnapshotRecorded": False,
                }
            }
