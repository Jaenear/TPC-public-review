"""Account operations, scoped roles, access grants and an audit trail."""

import json
from .. import access as access_model
from ..core import ApiError, text_field, choice_field, now_iso, new_id
from .common import require_role, row_dict, roles, audit, notify, page_args, version
from ..access import ensure_enrollment, grant, summary, period, public_grant, revoke


class AccountService:
    def __init__(self, app):
        self.app = app

    def instructors(self, user, query):
        page, limit, offset = page_args(query)
        with self.app.db.connect() as c:
            require_role(c, user, "admin")
            condition = " WHERE u.status='active' AND (u.role='admin' OR EXISTS(SELECT 1 FROM user_roles r WHERE r.user_id=u.id AND r.role='coach'))"
            rows = c.execute(
                "SELECT u.id,u.name,u.email,u.role FROM users u"
                + condition
                + " ORDER BY u.name,u.id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return {
                "users": [
                    {**row_dict(row), "roles": sorted(roles(c, row))} for row in rows
                ],
                "total": c.execute(
                    "SELECT COUNT(*) FROM users u" + condition
                ).fetchone()[0],
                "page": page,
                "pageSize": limit,
            }

    def users(self, user, query):
        page, limit, offset = page_args(query)
        search = query.get("q", [""])[0].strip()[:100]
        with self.app.db.connect() as c:
            require_role(c, user, "admin")
            where = " WHERE (u.name LIKE ? OR u.email LIKE ?)"
            args = ["%" + search + "%"] * 2
            total = c.execute("SELECT COUNT(*) FROM users u" + where, args).fetchone()[
                0
            ]
            rows = c.execute(
                "SELECT u.id,u.name,u.email,u.role,u.status,u.version,u.created_at,(SELECT COUNT(*) FROM enrollments e WHERE e.user_id=u.id) AS enrollmentCount FROM users u"
                + where
                + " ORDER BY u.created_at DESC,u.id LIMIT ? OFFSET ?",
                args + [limit, offset],
            ).fetchall()
            return {
                "users": [
                    {**row_dict(row), "roles": sorted(roles(c, row))} for row in rows
                ],
                "page": page,
                "pageSize": limit,
                "total": total,
            }

    def details(self, user, user_id):
        with self.app.db.connect() as c:
            require_role(c, user, "admin")
            target = c.execute(
                "SELECT id,name,email,role,status,version,created_at FROM users WHERE id=?",
                (user_id,),
            ).fetchone()
            if not target:
                raise ApiError(404, "사용자를 찾을 수 없습니다.")
            access = c.execute(
                "SELECT e.id,e.course_id,co.title FROM enrollments e JOIN courses co ON co.id=e.course_id WHERE e.user_id=? ORDER BY e.created_at DESC,e.id",
                (user_id,),
            ).fetchall()
            return {
                "user": {**row_dict(target), "roles": sorted(roles(c, target))},
                "access": [
                    {**row_dict(row), **summary(c, row["id"])} for row in access
                ],
            }

    def update(self, user, user_id, data):
        reason = text_field(data, "reason", minimum=5, maximum=2000)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            require_role(c, user, "admin")
            target = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not target:
                raise ApiError(404, "사용자를 찾을 수 없습니다.")
            version(data, target)
            role = choice_field(data, "role", ("student", "admin"))
            status = choice_field(data, "status", ("active", "suspended"))
            requested = data.get("roles", [])
            if (
                not isinstance(requested, list)
                or any(r not in ("coach", "moderator") for r in requested)
                or len(set(requested)) != len(requested)
            ):
                raise ApiError(400, "사용자 역할을 확인해주세요.")
            if user_id == user["id"]:
                raise ApiError(
                    409, "자신의 권한이나 상태는 이 화면에서 변경할 수 없습니다."
                )
            if (
                target["role"] == "admin"
                and target["status"] == "active"
                and (role != "admin" or status != "active")
                and c.execute(
                    "SELECT COUNT(*) FROM users WHERE role='admin' AND status='active' AND id<>?",
                    (user_id,),
                ).fetchone()[0]
                == 0
            ):
                raise ApiError(409, "마지막 활성 관리자를 변경할 수 없습니다.")
            c.execute(
                "UPDATE users SET role=?,status=?,version=version+1 WHERE id=?",
                (role, status, user_id),
            )
            c.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
            for assigned in requested:
                c.execute("INSERT INTO user_roles VALUES(?,?)", (user_id, assigned))
            if "coach" not in requested and role != "admin":
                c.execute(
                    "UPDATE courses SET instructor_user_id=NULL,version=version+1,updated_at=? WHERE instructor_user_id=?",
                    (now_iso(), user_id),
                )
            # Revoke sessions on any role edit so stale tabs must authenticate again.
            c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            audit(
                c,
                user,
                "user.updated",
                "user",
                user_id,
                {"reason": reason, "status": status, "role": role, "roles": requested},
            )
            return {
                "user": {
                    **{key: target[key] for key in ("id", "name", "email")},
                    "role": role,
                    "status": status,
                    "roles": requested,
                    "version": target["version"] + 1,
                }
            }

    def access(self, user, data):
        target_id = text_field(data, "userId", maximum=100)
        course_id = text_field(data, "courseId", maximum=100)
        status = choice_field(data, "status", ("active", "revoked"))
        reason = text_field(data, "reason", minimum=5, maximum=2000)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            require_role(c, user, "admin")
            if (
                not c.execute("SELECT 1 FROM users WHERE id=?", (target_id,)).fetchone()
                or not c.execute(
                    "SELECT 1 FROM courses WHERE id=? AND status IN ('published','archived')",
                    (course_id,),
                ).fetchone()
            ):
                raise ApiError(404, "사용자 또는 공개된 강의를 찾을 수 없습니다.")
            enrollment = ensure_enrollment(c, target_id, course_id)["id"]
            before = summary(c, enrollment)
            details = {"reason": reason, "userId": target_id, "courseId": course_id}
            changed = True
            if status == "active":
                request_id = text_field(data, "requestId", maximum=100, optional=True)
                source_id = (
                    user["id"] + ":" + request_id if request_id else new_id("manual")
                )
                existing = c.execute(
                    "SELECT * FROM enrollment_grants WHERE enrollment_id=? AND source='manual' AND source_id=?",
                    (enrollment, source_id),
                ).fetchone()
                dates = period(
                    data,
                    (
                        (existing["starts_at"], existing["expires_at"])
                        if existing
                        else None
                    ),
                )
                issued = grant(
                    c,
                    enrollment,
                    "manual",
                    source_id,
                    reason,
                    *dates,
                    actor_id=user["id"]
                )
                details.update(
                    grantId=issued["id"], startsAt=dates[0], expiresAt=dates[1]
                )
                changed = existing is None
            else:
                if (
                    "accessVersion" in data
                    and data["accessVersion"] != before["version"]
                ):
                    raise ApiError(
                        409,
                        "수강권 구성이 변경되었습니다. 최신 목록을 확인한 뒤 전체 회수해주세요.",
                    )
                rows = c.execute(
                    "SELECT * FROM enrollment_grants WHERE enrollment_id=?",
                    (enrollment,),
                ).fetchall()
                if not rows:
                    rows = [
                        grant(
                            c,
                            enrollment,
                            "manual",
                            new_id("manual"),
                            reason,
                            starts_at=now_iso(),
                            actor_id=user["id"],
                        )
                    ]
                details["grantIds"] = [
                    row["id"] for row in rows if revoke(c, row, reason, user["id"])
                ]
                changed = bool(details["grantIds"])
            if changed:
                notice_event = audit(
                    c, user, "access." + status, "enrollment", enrollment, details
                )
                notify(
                    c,
                    target_id,
                    "수강권 상태가 변경되었습니다",
                    reason,
                    "#/my",
                    event_key="access.change:" + notice_event,
                    category="operation",
                    reference_kind="enrollment",
                    reference_id=enrollment,
                    group_key="course:" + course_id,
                )
            return {
                "access": {
                    "userId": target_id,
                    "courseId": course_id,
                    **summary(c, enrollment),
                }
            }

    def update_grant(self, user, grant_id, data):
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            require_role(c, user, "admin")
            row = c.execute(
                "SELECT g.*,e.user_id,e.course_id FROM enrollment_grants g JOIN enrollments e ON e.id=g.enrollment_id WHERE g.id=?",
                (grant_id,),
            ).fetchone()
            if not row:
                raise ApiError(404, "수강권을 찾을 수 없습니다.")
            version(data, row)
            action = choice_field(data, "action", ("revoke", "period"))
            reason = text_field(data, "reason", minimum=5, maximum=2000)
            if row["status"] == "revoked":
                raise ApiError(
                    409,
                    "이미 회수된 수강권입니다. 복구가 필요하면 사유를 남겨 새로 발급해주세요.",
                )
            at = access_model.now_iso()
            before = public_grant(row, at)
            if action == "period":
                if row["source"] != "manual":
                    raise ApiError(
                        403, "운영자가 발급한 수강권의 기간만 직접 변경할 수 있습니다."
                    )
                start, end = period(data, (row["starts_at"] or at, row["expires_at"]))
                c.execute(
                    "UPDATE enrollment_grants SET starts_at=?,expires_at=?,updated_at=?,updated_by=?,version=version+1 WHERE id=?",
                    (start, end, at, user["id"], grant_id),
                )
            else:
                revoke(c, row, reason, user["id"])
            after = public_grant(
                c.execute(
                    "SELECT * FROM enrollment_grants WHERE id=?", (grant_id,)
                ).fetchone(),
                at,
            )
            notice_event = audit(
                c,
                user,
                "access.grant." + action,
                "enrollment_grant",
                grant_id,
                {
                    "reason": reason,
                    "userId": row["user_id"],
                    "courseId": row["course_id"],
                    "before": before,
                    "after": after,
                },
            )
            notify(
                c,
                row["user_id"],
                "수강권 상태가 변경되었습니다",
                reason,
                "#/my",
                event_key="access.change:" + notice_event,
                category="operation",
                reference_kind="enrollment",
                reference_id=row["enrollment_id"],
                group_key="course:" + row["course_id"],
            )
            return {"grant": after, "access": summary(c, row["enrollment_id"])}

    def audit(self, user, query):
        page, limit, offset = page_args(query)
        with self.app.db.connect() as c:
            require_role(c, user, "admin")
            rows = c.execute(
                "SELECT a.*,u.name AS actor FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.created_at DESC,a.id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return {
                "events": [
                    {**row_dict(row), "details": json.loads(row["details"])}
                    for row in rows
                ],
                "total": c.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0],
                "page": page,
                "pageSize": limit,
            }

    def notifications(self, user):
        return self.app.platform.notifications.listing(user, {})

    def read_notifications(self, user, data):
        return self.app.platform.notifications.read(user, data)
