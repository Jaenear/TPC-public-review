"""An appeal has one current head and an immutable, source-bound event chain."""

import json
from ..core import ApiError, new_id, now_iso, text_field, choice_field
from ..moderation_records import canonical, digest, observe
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

OPEN = ("submitted", "reviewing")


def snapshot(item):
    return {
        "id": item["id"],
        "decisionId": item["current_decision_id"],
        "actionId": item["action_id"],
        "appellantId": item["appellant_id"],
        "submissionReason": item["submission_reason"],
        "status": item["status"],
        "version": item["version"],
        "reviewerId": item["reviewer_id"],
        "reviewTarget": json.loads(item["review_snapshot"]),
        "reviewPolicy": (
            json.loads(item["review_policy_snapshot"])
            if item["review_policy_snapshot"]
            else None
        ),
        "needsReview": bool(item["needs_review"]),
        "createdAt": item["created_at"],
        "updatedAt": item["updated_at"],
    }


class ModerationAppeals:
    def __init__(self, moderation):
        self.m = moderation
        self.app = moderation.app

    def get(self, c, appeal_id):
        found = c.execute(
            "SELECT * FROM moderation_appeals WHERE id=?", (appeal_id,)
        ).fetchone()
        if not found:
            raise ApiError(404, "현재 확인할 수 있는 이의를 찾을 수 없습니다.")
        return found

    def scope(self, c, user, appeal_id, staff=False):
        user = actor(c, user, "moderator" if staff else None)
        found = self.get(c, appeal_id)
        if not staff and found["appellant_id"] != user["id"]:
            raise ApiError(404, "현재 확인할 수 있는 이의를 찾을 수 없습니다.")
        event = c.execute(
            "SELECT after_hash FROM moderation_decisions WHERE id=? AND appeal_id=? AND result_version=? AND result_status=?",
            (
                found["current_decision_id"],
                appeal_id,
                found["version"],
                found["status"],
            ),
        ).fetchone()
        if not event or event[0] != digest(snapshot(found)):
            raise ApiError(409, "현재 이의와 불변 결정 기준본을 확인해주세요.")
        _, source = self.m.action_scope(c, user, found["action_id"], staff)
        return user, found, source

    def eligible(self, c, source, candidate):
        if (
            not candidate
            or candidate["status"] != "active"
            or not roles(c, candidate).intersection({"admin", "moderator"})
        ):
            return False
        target = json.loads(source["after_snapshot"])
        related = {source["actor_id"], source["target_user_id"], target["postUserId"]}
        # Direct actions need not select a report. All reporters on this post,
        # including a report made after assignment, remain involved parties.
        reported = c.execute(
            "SELECT 1 FROM reports WHERE user_id=? AND (post_id=? OR id=?) LIMIT 1",
            (candidate["id"], source["post_id"], source["report_id"]),
        ).fetchone()
        return candidate["id"] not in related and reported is None

    def candidates(self, user, action_id):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user, source = self.m.action_scope(c, user, action_id, True)
            people = c.execute(
                "SELECT * FROM users u WHERE u.status='active' AND (u.role='admin' OR EXISTS(SELECT 1 FROM user_roles r WHERE r.user_id=u.id AND r.role='moderator')) ORDER BY u.name,u.id"
            ).fetchall()
            return {
                "reviewers": [
                    {"id": p["id"], "name": p["name"], "role": actor_role(c, p)}
                    for p in people
                    if self.eligible(c, source, p)
                ]
            }

    def view(self, c, user, item, source, staff=False, history_page=None):
        result = snapshot(item)
        result["action"] = self.m.action_view(c, user, source, staff)
        result["currentTarget"] = observe(c, source["kind"], source["target_id"])
        result["currentTargetHash"] = digest(result["currentTarget"])
        result["currentPolicy"] = self.m.policies.current(c)
        if staff and history_page is not None:
            result["currentContent"] = self.m.content(c, result["currentTarget"])
        if staff and item["reviewer_id"]:
            reviewer = c.execute(
                "SELECT * FROM users WHERE id=?", (item["reviewer_id"],)
            ).fetchone()
            result["reviewer"] = {
                "id": reviewer["id"],
                "name": reviewer["name"],
                "eligible": self.eligible(c, source, reviewer),
            }
        result.update(
            canWithdraw=item["appellant_id"] == user["id"] and item["status"] in OPEN,
            canAssign=staff and item["status"] in OPEN,
            canDecide=staff
            and item["status"] == "reviewing"
            and item["reviewer_id"] == user["id"]
            and self.eligible(c, source, user),
            appealDeadline=None,
            reapplicationReady=False,
            decisionGate="DEC-10",
        )
        if history_page is not None:
            result["decisions"] = []
            result["decisionPage"] = history_page
            result["decisionPageSize"] = 20
            result["decisionTotal"] = c.execute(
                "SELECT COUNT(*) FROM moderation_decisions WHERE appeal_id=?",
                (item["id"],),
            ).fetchone()[0]
            for event in c.execute(
                "SELECT d.*,u.name AS actor_name FROM moderation_decisions d JOIN users u ON u.id=d.actor_id WHERE appeal_id=? ORDER BY result_version DESC,d.id DESC LIMIT 20 OFFSET ?",
                (item["id"], (history_page - 1) * 20),
            ):
                result["decisions"].append(
                    {
                        "id": event["id"],
                        "action": event["action"],
                        "actorRole": event["actor_role"],
                        "expectedVersion": event["expected_version"],
                        "resultVersion": event["result_version"],
                        "status": event["result_status"],
                        "reason": event["reason"],
                        "before": (
                            json.loads(event["before_snapshot"])
                            if event["before_snapshot"]
                            else None
                        ),
                        "after": json.loads(event["after_snapshot"]),
                        "beforeHash": event["before_hash"],
                        "afterHash": event["after_hash"],
                        "context": json.loads(event["context"]),
                        "resolutionActionId": event["resolution_action_id"],
                        "createdAt": event["created_at"],
                        **(
                            {
                                "actor": {
                                    "id": event["actor_id"],
                                    "name": event["actor_name"],
                                }
                            }
                            if staff
                            else {}
                        ),
                    }
                )
        return result

    def listing(self, user, query, staff=False, appeal_id=None):
        number, limit, offset = page(query)
        status_filter = query.get("status", [None])[0]
        if status_filter is not None and status_filter not in OPEN + (
            "accepted",
            "rejected",
            "withdrawn",
        ):
            raise ApiError(400, "이의 상태를 확인해주세요.")
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user = actor(c, user, "moderator" if staff else None)
            if appeal_id:
                if status_filter is not None:
                    raise ApiError(400, "개별 이의 이력은 페이지로 조회해주세요.")
                user, item, source = self.scope(c, user, appeal_id, staff)
                return {"appeal": self.view(c, user, item, source, staff, number)}
            where, args = ([] if staff else ["appellant_id=?"]), (
                [] if staff else [user["id"]]
            )
            if status_filter:
                where.append("status=?")
                args.append(status_filter)
            clause = " WHERE " + " AND ".join(where) if where else ""
            total = c.execute(
                "SELECT COUNT(*) FROM moderation_appeals" + clause, args
            ).fetchone()[0]
            rows = c.execute(
                "SELECT * FROM moderation_appeals"
                + clause
                + " ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?",
                args + [limit, offset],
            ).fetchall()
            results = []
            for item in rows:
                _, _, source = self.scope(c, user, item["id"], staff)
                results.append(self.view(c, user, item, source, staff))
            return {
                "appeals": results,
                "page": number,
                "pageSize": limit,
                "total": total,
            }

    def event(
        self, c, user, before, after, action, reason, resolution=None, context=None
    ):
        previous = snapshot(before) if before else None
        current = snapshot(after)
        eid = after["current_decision_id"]
        c.execute(
            "INSERT INTO moderation_decisions(id,appeal_id,actor_id,actor_role,action,expected_version,result_version,result_status,reason,before_snapshot,after_snapshot,before_hash,after_hash,resolution_action_id,context,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                eid,
                after["id"],
                user["id"],
                actor_role(c, user),
                action,
                before["version"] if before else 0,
                after["version"],
                after["status"],
                reason,
                canonical(previous) if previous else None,
                canonical(current),
                digest(previous) if previous else None,
                digest(current),
                resolution,
                canonical(context or {}),
                now_iso(),
            ),
        )
        audit_entry(
            c,
            user,
            "moderation.appeal_" + action,
            "moderation_appeal",
            after["id"],
            {
                "reason": reason,
                "version": after["version"],
                "decisionId": eid,
                "resolutionActionId": resolution,
            },
        )
        return eid

    def create(self, user, action_id, data):
        limit(self.app, user, "moderation-appeal", 20)
        fields(
            data,
            {"requestKey", "expectedVersion", "reason"},
            {"requestKey", "expectedVersion", "reason"},
        )
        key, hashed = request(data, "appeal.create", action_id)
        version, reason = expected(data), text_field(
            data, "reason", minimum=5, maximum=2000
        )
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, source = self.m.action_scope(c, user, action_id)
            saved = replay(c, user, key, hashed)
            if saved:
                status, result = saved
                item = self.get(c, result["receipt"]["appealId"])
                return status, {
                    **result,
                    "currentAppeal": self.view(c, user, item, source),
                }
            if source["result_version"] != version:
                raise ApiError(409, "이의를 제기할 조치 판본을 확인해주세요.")
            if not self.m.adverse(source):
                raise ApiError(409, "이의 대상인 제한 조치를 확인해주세요.")
            if c.execute(
                "SELECT 1 FROM moderation_appeals WHERE action_id=? AND appellant_id=?",
                (action_id, user["id"]),
            ).fetchone():
                raise ApiError(
                    409,
                    "이미 접수된 이의가 있습니다. 재접수 기준은 DEC-10 확인이 필요합니다.",
                )
            original = json.loads(source["after_snapshot"])
            policy = (
                json.loads(source["policy_snapshot"])
                if source["policy_snapshot"]
                else None
            )
            current = observe(c, source["kind"], source["target_id"])
            changed = original != current or policy != self.m.policies.current(c)
            aid, at, first_event = new_id("modappeal"), now_iso(), new_id("moddecision")
            c.execute(
                "INSERT INTO moderation_appeals(id,current_decision_id,action_id,appellant_id,submission_reason,review_snapshot,review_policy_id,review_policy_snapshot,needs_review,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    aid,
                    first_event,
                    action_id,
                    user["id"],
                    reason,
                    canonical(original),
                    source["policy_id"],
                    source["policy_snapshot"],
                    int(changed),
                    at,
                    at,
                ),
            )
            item = self.get(c, aid)
            eid = self.event(c, user, None, item, "submitted", reason)
            status, result = remember(
                c,
                user,
                key,
                hashed,
                "appeal.create",
                action_id,
                {"id": eid, "appealId": aid, "version": 1, "status": "submitted"},
                decision_id=eid,
            )
            return status, {**result, "currentAppeal": self.view(c, user, item, source)}

    def change(self, user, appeal_id, data, staff=True):
        limit(self.app, user, "moderation-appeal-change", 120)
        fields(
            data,
            {
                "requestKey",
                "expectedVersion",
                "action",
                "reason",
                "reviewerId",
                "expectedTargetVersion",
                "expectedContentHash",
                "expectedTargetHash",
                "policyId",
            },
            {"requestKey", "expectedVersion", "action", "reason"},
        )
        key, hashed = request(data, "appeal.change", appeal_id)
        version, reason = expected(data), text_field(
            data, "reason", minimum=5, maximum=2000
        )
        action = choice_field(
            data, "action", ("assign", "recheck", "accept", "reject", "withdraw")
        )
        extras = (
            {"reviewerId"}
            if action == "assign"
            else (
                {
                    "expectedTargetVersion",
                    "expectedContentHash",
                    "expectedTargetHash",
                    "policyId",
                }
                if action == "recheck"
                else set()
            )
        )
        fields(
            data,
            {"requestKey", "expectedVersion", "action", "reason"} | extras,
            {"requestKey", "expectedVersion", "action", "reason"}
            | (extras - {"expectedTargetHash"}),
        )
        if not staff and action != "withdraw":
            raise ApiError(403, "본인의 이의 철회만 요청할 수 있습니다.")
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user, old, source = self.scope(c, user, appeal_id, staff)
            if action == "withdraw":
                if old["appellant_id"] != user["id"]:
                    raise ApiError(403, "본인이 접수한 이의만 철회할 수 있습니다.")
            elif action in ("recheck", "accept", "reject") and (
                old["reviewer_id"] != user["id"] or not self.eligible(c, source, user)
            ):
                raise ApiError(403, "배정된 별도 검토자만 결정할 수 있습니다.")
            saved = replay(c, user, key, hashed)
            if saved:
                status, result = saved
                return status, {
                    **result,
                    "currentAppeal": self.view(c, user, old, source, staff),
                }
            if old["version"] != version or old["status"] not in OPEN:
                raise ApiError(409, "이미 끝났거나 변경된 이의 상태를 확인해주세요.")
            reviewer = old["reviewer_id"]
            review = json.loads(old["review_snapshot"])
            policy = (
                json.loads(old["review_policy_snapshot"])
                if old["review_policy_snapshot"]
                else None
            )
            status_name, event_name = old["status"], action
            needs = bool(old["needs_review"])
            context, resolution, conflict = {}, None, False
            current = observe(c, source["kind"], source["target_id"])
            current_policy = self.m.policies.current(c)
            if action == "assign":
                if not isinstance(data["reviewerId"], str):
                    raise ApiError(400, "검토자를 확인해주세요.")
                candidate = c.execute(
                    "SELECT * FROM users WHERE id=?", (data["reviewerId"],)
                ).fetchone()
                if not self.eligible(c, source, candidate):
                    raise ApiError(
                        403,
                        "최초 조치자·작성자·신고자와 분리된 현재 운영자를 배정해주세요.",
                    )
                reviewer, status_name, event_name = (
                    candidate["id"],
                    "reviewing",
                    "assigned",
                )
            elif action == "withdraw":
                status_name, event_name = "withdrawn", "withdrawn"
            else:
                if old["status"] != "reviewing":
                    raise ApiError(409, "배정된 검토 상태에서 진행해주세요.")
                if action == "recheck":
                    if (
                        expected(data, "expectedTargetVersion") != current["version"]
                        or data["expectedContentHash"] != current["contentHash"]
                        or (
                            "expectedTargetHash" in data
                            and data["expectedTargetHash"] != digest(current)
                        )
                    ):
                        raise ApiError(409, "검토한 최신 원문이 다시 변경되었습니다.")
                    self.m.policies.capture(c, data["policyId"], True)
                    review, policy, needs, event_name = (
                        current,
                        current_policy,
                        False,
                        "rechecked",
                    )
                elif action == "reject":
                    status_name, event_name = "rejected", "rejected"
                else:
                    original = json.loads(source["after_snapshot"])
                    field = "locked" if source["action"] == "lock" else "status"
                    if (
                        current != review
                        or current_policy != policy
                        or current.get(field) != original.get(field)
                        or current.get("ownerDeleted")
                        or current.get("postStatus", "published") != "published"
                    ):
                        conflict, needs, event_name = True, True, "conflict"
                        context = {
                            "target": current,
                            "policy": current_policy,
                            "cause": "current_source_or_policy_changed",
                        }
                    else:
                        inverse = {
                            "hide": "restore",
                            "lock": "unlock",
                            "comment_delete": "comment_restore",
                        }[source["action"]]
                        after = self.m.apply(c, current, inverse)
                        resolved = self.m.append_action(
                            c,
                            user,
                            current,
                            after,
                            inverse,
                            reason,
                            policy,
                            origin="appeal",
                            source_action_id=source["id"],
                            inform=False,
                        )
                        resolution, status_name, event_name = (
                            resolved["id"],
                            "accepted",
                            "accepted",
                        )
            at = now_iso()
            next_event = new_id("moddecision")
            c.execute(
                "UPDATE moderation_appeals SET current_decision_id=?,status=?,version=version+1,reviewer_id=?,review_snapshot=?,review_policy_id=?,review_policy_snapshot=?,needs_review=?,updated_at=? WHERE id=? AND version=?",
                (
                    next_event,
                    status_name,
                    reviewer,
                    canonical(review),
                    policy["id"] if policy else None,
                    canonical(policy) if policy else None,
                    int(needs),
                    at,
                    appeal_id,
                    version,
                ),
            )
            item = self.get(c, appeal_id)
            eid = self.event(
                c, user, old, item, event_name, reason, resolution, context
            )
            if action == "assign" and reviewer != user["id"]:
                notify(
                    c,
                    reviewer,
                    "커뮤니티 이의 검토가 배정되었습니다",
                    "현재 운영 권한으로 접수 상태를 확인해주세요.",
                    "#/manage/moderation/appeals/" + appeal_id,
                    event_key="moderation.appeal:" + eid,
                    category="operation",
                    reference_kind="staff_appeal",
                    reference_id=appeal_id,
                    group_key="appeal:" + appeal_id,
                )
            if user["id"] != old["appellant_id"]:
                notify(
                    c,
                    old["appellant_id"],
                    "커뮤니티 이의 검토 상태가 변경되었습니다",
                    "현재 권한으로 조치 사유와 검토 상태를 확인해주세요.",
                    "#/community/moderation/appeals/" + appeal_id,
                    event_key="moderation.appeal:" + eid,
                    category="operation",
                    reference_kind="moderation_appeal",
                    reference_id=appeal_id,
                    group_key="appeal:" + appeal_id,
                )
            status, result = remember(
                c,
                user,
                key,
                hashed,
                "appeal.change",
                appeal_id,
                {
                    "id": eid,
                    "appealId": appeal_id,
                    "version": item["version"],
                    "status": item["status"],
                },
                decision_id=eid,
                conflict=conflict,
            )
            return status, {
                **result,
                "currentAppeal": self.view(c, user, item, source, staff),
            }
