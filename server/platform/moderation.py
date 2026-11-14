"""Versioned community actions, joined to existing IDs and audit records."""

import json
import re
from ..core import ApiError, new_id, now_iso, text_field, choice_field
from ..moderation_records import canonical, digest, observe, observe_report
from .common import notify, roles
from .moderation_shared import (
    actor,
    actor_role,
    fields,
    expected,
    request,
    replay,
    remember,
    audit_entry,
    page,
    limit,
)
from .moderation_policies import ModerationPolicies

POST_CHANGES = {
    "hide": ("status", "hidden"),
    "restore": ("status", "published"),
    "lock": ("locked", 1),
    "unlock": ("locked", 0),
    "pin": ("pinned", 1),
    "unpin": ("pinned", 0),
}


class ModerationService:
    def __init__(self, app, discussions):
        self.app, self.discussions = app, discussions
        self.policies = ModerationPolicies(app)
        from .moderation_appeals import ModerationAppeals

        self.appeals = ModerationAppeals(self)

    def adverse(self, item):
        before, after = json.loads(item["before_snapshot"]), json.loads(
            item["after_snapshot"]
        )
        return (
            (
                item["action"] == "hide"
                and before["status"] == "published"
                and after["status"] == "hidden"
            )
            or (item["action"] == "lock" and not before["locked"] and after["locked"])
            or (
                item["action"] == "comment_delete"
                and before["status"] == "published"
                and after["status"] == "deleted"
            )
        )

    def scope(self, c, user, kind, target_id, staff=False):
        user = actor(c, user, "moderator" if staff else None)
        target = observe(c, kind, target_id)
        self.discussions.board(c, user, target["boardId"])
        if not staff and target["userId"] != user["id"]:
            raise ApiError(404, "현재 확인할 수 있는 운영 대상을 찾을 수 없습니다.")
        return user, target

    def current_policy(self, c, data=None):
        data = data or {}
        return self.policies.capture(c, data.get("policyId"), "policyId" in data)

    def apply(self, c, before, action):
        kind, tid = before["kind"], before["id"]
        if action == "report_dismiss":
            return before
        if kind == "post":
            if action not in POST_CHANGES or before["status"] == "deleted":
                raise ApiError(409, "작성자 삭제 또는 현재 대상 상태를 확인해주세요.")
            field, value = POST_CHANGES[action]
            if action == "restore" and before["status"] != "hidden":
                raise ApiError(409, "운영 숨김 상태의 게시글만 복원할 수 있습니다.")
            if before[field] == value:
                raise ApiError(409, "이미 적용된 게시글 상태입니다.")
            changed = c.execute(
                "UPDATE posts SET "
                + field
                + "=?,version=version+1,updated_at=? WHERE id=? AND version=?",
                (value, now_iso(), tid, before["version"]),
            )
            if changed.rowcount != 1:
                raise ApiError(409, "다른 게시글 변경이 있습니다.")
        else:
            if action not in ("comment_delete", "comment_restore"):
                raise ApiError(400, "댓글 운영 항목을 확인해주세요.")
            wanted = "deleted" if action == "comment_delete" else "published"
            if before["ownerDeleted"] or before["status"] == wanted:
                raise ApiError(409, "작성자 삭제 또는 현재 댓글 상태를 확인해주세요.")
            changed = c.execute(
                "UPDATE moderation_comment_heads SET version=version+1 WHERE comment_id=? AND version=? AND owner_deleted=0",
                (tid, before["version"]),
            )
            if changed.rowcount != 1:
                raise ApiError(409, "다른 댓글 변경이 있습니다.")
            c.execute("UPDATE comments SET status=? WHERE id=?", (wanted, tid))
        return observe(c, kind, tid)

    def append_action(
        self,
        c,
        user,
        before,
        after,
        action,
        reason,
        policy,
        *,
        origin="explicit",
        report_id=None,
        source_action_id=None,
        source_audit_id=None,
        inform=True
    ):
        aid, at = new_id("modaction"), now_iso()
        if source_audit_id is None:
            source_audit_id = audit_entry(
                c,
                user,
                "moderation." + action,
                "moderation_action",
                aid,
                {
                    "targetId": before["id"],
                    "reason": reason,
                    "expectedVersion": before["version"],
                    "resultVersion": after["version"],
                    "sourceActionId": source_action_id,
                },
            )
        c.execute(
            "INSERT INTO moderation_actions(id,kind,target_id,post_id,target_user_id,actor_id,actor_role,report_id,source_audit_id,source_action_id,action,origin,expected_version,result_version,reason,before_snapshot,after_snapshot,before_hash,after_hash,policy_id,policy_snapshot,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                aid,
                before["kind"],
                before["id"],
                before["postId"],
                before["userId"],
                user["id"],
                actor_role(c, user),
                report_id,
                source_audit_id,
                source_action_id,
                action,
                origin,
                before["version"],
                after["version"],
                reason,
                canonical(before),
                canonical(after),
                digest(before),
                digest(after),
                policy["id"] if policy else None,
                canonical(policy) if policy else None,
                at,
            ),
        )
        if inform and user["id"] != before["userId"]:
            notify(
                c,
                before["userId"],
                "커뮤니티 운영 처리 내역이 있습니다",
                "현재 권한으로 조치 사유와 상태를 확인해주세요.",
                "#/community/moderation/actions/" + aid,
                event_key="moderation.action:" + aid,
                category="operation",
                reference_kind="moderation_action",
                reference_id=aid,
                group_key="post:" + before["postId"],
            )
        return c.execute(
            "SELECT * FROM moderation_actions WHERE id=?", (aid,)
        ).fetchone()

    def legacy(
        self,
        c,
        user,
        before,
        action,
        reason,
        *,
        policy=None,
        report_id=None,
        inform=False
    ):
        """Capture the genuine just-written legacy audit ID in its transaction."""
        user = actor(c, user, "moderator")
        source_audit_id = c.execute(
            "SELECT id FROM audit_logs WHERE rowid=last_insert_rowid()"
        ).fetchone()[0]
        after = observe(c, before["kind"], before["id"])
        return self.append_action(
            c,
            user,
            before,
            after,
            action,
            reason,
            policy,
            origin="legacy_endpoint",
            report_id=report_id,
            source_audit_id=source_audit_id,
            inform=inform,
        )

    def report_context(self, c, report, before, data):
        observed = c.execute(
            "SELECT * FROM moderation_report_observations WHERE report_id=?",
            (report["id"],),
        ).fetchone()
        if not observed:
            raise ApiError(409, "신고의 관찰 기준본을 확인해주세요.")
        if "expectedTargetVersion" in data or "expectedContentHash" in data:
            if (
                expected(data, "expectedTargetVersion") != before["version"]
                or data.get("expectedContentHash") != before["contentHash"]
            ):
                raise ApiError(409, "검토한 게시글 판본이 변경되었습니다.")
        elif observed["content_hash"] != before["contentHash"]:
            raise ApiError(
                409, "신고 뒤 원문이 변경됐습니다. 최신 판본을 명시적으로 검토해주세요."
            )

    def content(self, c, target):
        if target["kind"] == "post":
            found = c.execute(
                "SELECT title,body FROM posts WHERE id=?", (target["id"],)
            ).fetchone()
            return {"title": found["title"], "body": found["body"]}
        found = c.execute(
            "SELECT body FROM comments WHERE id=?", (target["id"],)
        ).fetchone()
        return {"body": found["body"]}

    def action_view(self, c, user, item, staff=False, content=False):
        before, after = json.loads(item["before_snapshot"]), json.loads(
            item["after_snapshot"]
        )
        prior = c.execute(
            "SELECT id,status FROM moderation_appeals WHERE action_id=? AND appellant_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (item["id"], item["target_user_id"]),
        ).fetchone()
        result = {
            "id": item["id"],
            "kind": item["kind"],
            "targetId": item["target_id"],
            "postId": item["post_id"],
            "action": item["action"],
            "origin": item["origin"],
            "reason": item["reason"],
            "actorRole": item["actor_role"],
            "expectedVersion": item["expected_version"],
            "resultVersion": item["result_version"],
            "before": before,
            "after": after,
            "beforeHash": item["before_hash"],
            "afterHash": item["after_hash"],
            "policy": (
                json.loads(item["policy_snapshot"]) if item["policy_snapshot"] else None
            ),
            "createdAt": item["created_at"],
            "canAppeal": user["id"] == item["target_user_id"]
            and self.adverse(item)
            and prior is None,
            "appeal": {"id": prior["id"], "status": prior["status"]} if prior else None,
            "appealDeadline": None,
            "reapplicationReady": False,
            "decisionGate": "DEC-10",
        }
        current = observe(c, item["kind"], item["target_id"])
        result.update(
            currentTarget=current,
            currentTargetHash=digest(current),
            currentPolicy=self.policies.current(c),
        )
        if staff and content:
            result["currentContent"] = self.content(c, current)
        if staff:
            person = c.execute(
                "SELECT name FROM users WHERE id=?", (item["actor_id"],)
            ).fetchone()
            result.update(
                actorId=item["actor_id"],
                actor={
                    "id": item["actor_id"],
                    "name": person[0],
                    "role": item["actor_role"],
                },
                targetUserId=item["target_user_id"],
                reportId=item["report_id"],
                sourceAuditId=item["source_audit_id"],
                sourceActionId=item["source_action_id"],
            )
        return result

    def action_scope(self, c, user, action_id, staff=False):
        user = actor(c, user, "moderator" if staff else None)
        found = c.execute(
            "SELECT * FROM moderation_actions WHERE id=?", (action_id,)
        ).fetchone()
        if not found or (not staff and found["target_user_id"] != user["id"]):
            raise ApiError(404, "현재 확인할 수 있는 조치를 찾을 수 없습니다.")
        self.discussions.board(c, user, json.loads(found["after_snapshot"])["boardId"])
        return user, found

    def actions(self, user, query, staff=False, action_id=None):
        number, limit, offset = page(query)
        if "status" in query:
            raise ApiError(400, "조치 조회 조건을 확인해주세요.")
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user = actor(c, user, "moderator" if staff else None)
            if action_id:
                user, item = self.action_scope(c, user, action_id, staff)
                return {"action": self.action_view(c, user, item, staff, True)}
            where, args = (
                ("", []) if staff else (" WHERE target_user_id=?", [user["id"]])
            )
            total = c.execute(
                "SELECT COUNT(*) FROM moderation_actions" + where, args
            ).fetchone()[0]
            rows = c.execute(
                "SELECT * FROM moderation_actions"
                + where
                + " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                args + [limit, offset],
            ).fetchall()
            return {
                "actions": [self.action_view(c, user, r, staff) for r in rows],
                "page": number,
                "pageSize": limit,
                "total": total,
            }

    def act(self, user, data):
        limit(self.app, user, "moderation-action")
        fields(
            data,
            {
                "requestKey",
                "kind",
                "targetId",
                "action",
                "expectedVersion",
                "expectedContentHash",
                "policyId",
                "reason",
                "reportId",
            },
            {
                "requestKey",
                "kind",
                "targetId",
                "action",
                "expectedVersion",
                "expectedContentHash",
                "policyId",
                "reason",
            },
        )
        kind = choice_field(data, "kind", ("post", "comment"))
        target_id = text_field(data, "targetId", maximum=100)
        action = choice_field(
            data, "action", tuple(POST_CHANGES) + ("comment_delete", "report_dismiss")
        )
        if (kind == "comment" and action != "comment_delete") or (
            kind == "post" and action == "comment_delete"
        ):
            raise ApiError(400, "대상과 운영 항목을 확인해주세요.")
        key, hashed = request(data, "action.create", target_id)
        version = expected(data)
        reason = text_field(data, "reason", minimum=5, maximum=2000)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, before = self.scope(c, user, kind, target_id, True)
            saved = replay(c, user, key, hashed)
            if saved:
                status, result = saved
                return status, {**result, "currentTarget": before}
            if (
                version != before["version"]
                or data["expectedContentHash"] != before["contentHash"]
            ):
                raise ApiError(409, "검토한 운영 대상의 판본이 변경되었습니다.")
            policy = self.current_policy(c, data)
            report_id = data.get("reportId")
            report = None
            if report_id is not None:
                report = c.execute(
                    "SELECT * FROM reports WHERE id=? AND post_id=?",
                    (report_id, before["postId"]),
                ).fetchone()
                if (
                    not report
                    or kind != "post"
                    or action not in ("hide", "report_dismiss")
                ):
                    raise ApiError(404, "같은 게시글의 신고를 확인해주세요.")
                if report["status"] != "open":
                    raise ApiError(409, "이미 처리된 신고입니다.")
            elif action == "report_dismiss":
                raise ApiError(400, "종결할 신고를 지정해주세요.")
            after = self.apply(c, before, action)
            if report:
                c.execute(
                    "UPDATE reports SET status='resolved',resolution=?,resolved_by=?,updated_at=? WHERE id=?",
                    (reason, user["id"], now_iso(), report_id),
                )
                notify(
                    c,
                    report["user_id"],
                    "신고 처리 결과가 등록되었습니다",
                    "현재 권한으로 처리 상태를 확인해주세요.",
                    "#/community",
                    event_key="report.result:" + report_id,
                    category="operation",
                    reference_kind="report",
                    reference_id=report_id,
                    group_key="report:" + report_id,
                )
            item = self.append_action(
                c,
                user,
                before,
                after,
                action,
                reason,
                policy,
                report_id=report_id,
                inform=action != "report_dismiss",
            )
            status, result = remember(
                c,
                user,
                key,
                hashed,
                "action.create",
                target_id,
                {"id": item["id"], "version": item["result_version"], "status": action},
                action_id=item["id"],
            )
            return status, {**result, "currentTarget": after}

    def target(self, user, kind, target_id):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            _, target = self.scope(c, user, kind, target_id, True)
            if kind == "post":
                item = c.execute(
                    "SELECT title,body FROM posts WHERE id=?", (target_id,)
                ).fetchone()
                content = {"title": item["title"], "body": item["body"]}
            else:
                item = c.execute(
                    "SELECT body FROM comments WHERE id=?", (target_id,)
                ).fetchone()
                content = {"body": item["body"]}
            return {
                "target": target,
                "content": content,
                "policy": self.policies.current(c),
                "decisionGate": "DEC-10",
            }

    def route(self, h, path, method, query):
        if not path.startswith(
            ("/api/community/moderation/", "/api/manage/moderation/")
        ):
            return None
        user = h.user
        if method not in ("GET", "POST", "PATCH") or (method != "GET" and query):
            raise ApiError(
                405 if method not in ("GET", "POST", "PATCH") else 400,
                "운영 API의 요청 방식/조건을 확인해주세요.",
            )

        def response(result):
            if isinstance(result, tuple):
                return result[0], result[1], {}
            return 200, result, {}

        if path == "/api/community/moderation/rules":
            if method != "GET" or query:
                raise ApiError(
                    405 if method != "GET" else 400,
                    "현재 운영 규칙은 조회로 확인해주세요.",
                )
            return response(self.policies.listing(user, True))
        if path == "/api/manage/moderation/policies":
            if query:
                raise ApiError(400, "규칙 조회 조건을 확인해주세요.")
            if method == "GET":
                return response(self.policies.listing(user))
            if method == "POST":
                return response(self.policies.create(user, h.body()))
        match = re.fullmatch(
            r"/api/manage/moderation/targets/(post|comment)/([a-z0-9_]+)", path
        )
        if match and method == "GET" and not query:
            return response(self.target(user, match.group(1), match.group(2)))
        match = re.fullmatch(r"/api/manage/moderation/policies/([a-z0-9_]+)", path)
        if match:
            if method == "GET" and not query:
                return response(self.policies.detail(user, match.group(1)))
            if method == "PATCH":
                return response(self.policies.change(user, match.group(1), h.body()))
        for audience in ("community", "manage"):
            base, staff = "/api/" + audience + "/moderation", audience == "manage"
            if path == base + "/actions":
                if method == "GET":
                    return response(self.actions(user, query, staff))
                if staff and method == "POST":
                    return response(self.act(user, h.body()))
            match = re.fullmatch(
                re.escape(base) + r"/actions/([a-z0-9_]+)(?:/(appeals|reviewers))?",
                path,
            )
            if match:
                aid, suffix = match.groups()
                if suffix is None and method == "GET":
                    return response(self.actions(user, query, staff, aid))
                if suffix == "appeals" and not staff and method == "POST":
                    return response(self.appeals.create(user, aid, h.body()))
                if suffix == "reviewers" and staff and method == "GET" and not query:
                    return response(self.appeals.candidates(user, aid))
            if path == base + "/appeals" and method == "GET":
                return response(self.appeals.listing(user, query, staff))
            match = re.fullmatch(re.escape(base) + r"/appeals/([a-z0-9_]+)", path)
            if match:
                if method == "GET":
                    return response(
                        self.appeals.listing(user, query, staff, match.group(1))
                    )
                if method == "PATCH":
                    return response(
                        self.appeals.change(user, match.group(1), h.body(), staff)
                    )
        raise ApiError(405, "지원하는 운영 API의 경로와 요청 방식을 확인해주세요.")
