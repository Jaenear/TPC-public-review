"""Explicit operator-authored editions. Nothing is seeded or auto-approved."""

from ..access import canonical_time
from ..core import ApiError, text_field, choice_field, new_id, now_iso
from ..moderation_records import digest
from .moderation_shared import (
    actor,
    fields,
    expected,
    request,
    replay,
    remember,
    audit_entry,
    limit,
)


class ModerationPolicies:
    def __init__(self, app):
        self.app = app

    def get(self, c, policy_id):
        item = c.execute(
            "SELECT p.*,s.status,s.version,s.reason,s.published_at FROM moderation_policy_versions p JOIN moderation_policy_states s ON s.policy_id=p.id WHERE p.id=?",
            (policy_id,),
        ).fetchone()
        if not item:
            raise ApiError(404, "운영 규칙 판본을 찾을 수 없습니다.")
        return item

    def view(self, item, private=False):
        if item is None:
            return None
        result = {
            "id": item["id"],
            "mode": item["mode"],
            "edition": item["edition"],
            "title": item["title"],
            "body": item["body"],
            "contentHash": item["content_hash"],
            "effectiveAt": item["effective_at"],
            "expiresAt": item["expires_at"],
            "status": item["status"],
            "version": item["version"],
        }
        if private:
            result.update(
                approvalReference=item["approval_reference"],
                reason=item["reason"],
                publishedAt=item["published_at"],
            )
        return result

    def current(self, c, mode="live"):
        found = c.execute(
            "SELECT policy_id FROM moderation_policy_states WHERE mode=? AND status='current'",
            (mode,),
        ).fetchone()
        if not found:
            return None
        item = self.get(c, found[0])
        at = now_iso()
        if (item["effective_at"] and item["effective_at"] > at) or (
            item["expires_at"] and item["expires_at"] <= at
        ):
            return None
        return self.view(item)

    def capture(self, c, supplied=None, explicit=False):
        policy = self.current(c)
        if explicit and supplied != (policy["id"] if policy else None):
            raise ApiError(409, "현재 적용되는 운영 규칙을 다시 확인해주세요.")
        return policy

    def listing(self, user, public=False):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            if public:
                return {
                    "policy": self.current(c),
                    "decisionGate": "DEC-10",
                    "appealDeadline": None,
                    "reapplicationReady": False,
                }
            actor(c, user, "admin")
            rows = c.execute(
                "SELECT id FROM moderation_policy_versions ORDER BY created_at DESC,id DESC LIMIT 200"
            ).fetchall()
            return {
                "policies": [self.view(self.get(c, r[0]), True) for r in rows],
                "currentPolicy": self.current(c),
                "decisionGate": "DEC-10",
            }

    def detail(self, user, policy_id):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor(c, user, "admin")
            return {"policy": self.view(self.get(c, policy_id), True)}

    def create(self, user, data):
        limit(self.app, user, "moderation-policy", 30)
        allowed = {
            "requestKey",
            "mode",
            "edition",
            "title",
            "body",
            "approvalReference",
            "effectiveAt",
            "expiresAt",
        }
        fields(data, allowed, {"requestKey", "mode", "edition", "title", "body"})
        key, hashed = request(data, "policy.create", "new")
        mode = choice_field(data, "mode", ("test", "live"))
        values = {
            "mode": mode,
            "edition": text_field(data, "edition", maximum=100),
            "title": text_field(data, "title", minimum=3, maximum=150),
            "body": text_field(data, "body", minimum=20, maximum=20000),
        }
        approval = (
            text_field(data, "approvalReference", minimum=5, maximum=500)
            if mode == "live"
            else text_field(data, "approvalReference", maximum=500, optional=True)
        )
        for name in ("effectiveAt", "expiresAt"):
            value = data.get(name)
            values[name] = (
                canonical_time(value, "운영 규칙 적용 시각")
                if value is not None
                else None
            )
        if (
            values["effectiveAt"]
            and values["expiresAt"]
            and values["expiresAt"] <= values["effectiveAt"]
        ):
            raise ApiError(400, "규칙 종료 시각은 시작보다 뒤여야 합니다.")
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = actor(c, user, "admin")
            saved = replay(c, user, key, hashed)
            if saved:
                status, result = saved
                return status, {
                    **result,
                    "policy": self.view(self.get(c, result["receipt"]["id"]), True),
                }
            if c.execute(
                "SELECT 1 FROM moderation_policy_versions WHERE mode=? AND edition=?",
                (mode, values["edition"]),
            ).fetchone():
                raise ApiError(409, "이미 보관된 운영 규칙 판본입니다.")
            pid, at = new_id("modpolicy"), now_iso()
            c.execute(
                "INSERT INTO moderation_policy_versions(id,mode,edition,title,body,approval_reference,effective_at,expires_at,content_hash,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pid,
                    mode,
                    values["edition"],
                    values["title"],
                    values["body"],
                    approval,
                    values["effectiveAt"],
                    values["expiresAt"],
                    digest(values),
                    user["id"],
                    at,
                ),
            )
            c.execute(
                "INSERT INTO moderation_policy_states(policy_id,mode,updated_by,updated_at) VALUES(?,?,?,?)",
                (pid, mode, user["id"], at),
            )
            audit_entry(
                c,
                user,
                "moderation.policy_created",
                "moderation_policy",
                pid,
                {"edition": values["edition"], "mode": mode},
            )
            status, result = remember(
                c,
                user,
                key,
                hashed,
                "policy.create",
                pid,
                {"id": pid, "version": 1, "status": "draft"},
                policy_id=pid,
            )
            return status, {
                **result,
                "policy": self.view(self.get(c, pid), True),
            }

    def change(self, user, policy_id, data):
        limit(self.app, user, "moderation-policy", 30)
        fields(
            data,
            {"requestKey", "expectedVersion", "action", "reason", "expectedCurrentId"},
            {"requestKey", "expectedVersion", "action", "reason", "expectedCurrentId"},
        )
        key, hashed = request(data, "policy.change", policy_id)
        version = expected(data)
        action = choice_field(data, "action", ("publish", "withdraw"))
        reason = text_field(data, "reason", minimum=5, maximum=2000)
        if data["expectedCurrentId"] is not None and not isinstance(
            data["expectedCurrentId"], str
        ):
            raise ApiError(400, "현재 적용 판본을 확인해주세요.")
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = actor(c, user, "admin")
            item = self.get(c, policy_id)
            saved = replay(c, user, key, hashed)
            if saved:
                status, result = saved
                return status, {**result, "policy": self.view(item, True)}
            current = c.execute(
                "SELECT policy_id,version FROM moderation_policy_states WHERE mode=? AND status='current'",
                (item["mode"],),
            ).fetchone()
            if item["version"] != version or data["expectedCurrentId"] != (
                current[0] if current else None
            ):
                raise ApiError(409, "운영 규칙 상태가 변경되었습니다.")
            if (action == "publish" and item["status"] != "draft") or (
                action == "withdraw"
                and item["status"] not in ("draft", "current", "superseded")
            ):
                raise ApiError(409, "현재 판본 상태에서 진행할 수 없습니다.")
            at = now_iso()
            if action == "publish" and (
                (item["effective_at"] and item["effective_at"] > at)
                or (item["expires_at"] and item["expires_at"] <= at)
            ):
                raise ApiError(409, "현재 적용할 수 있는 규칙 시각을 확인해주세요.")
            if action == "publish" and current:
                c.execute(
                    "UPDATE moderation_policy_states SET status='superseded',version=version+1,reason=?,updated_by=?,updated_at=? WHERE policy_id=?",
                    (reason, user["id"], at, current[0]),
                )
            status_name = "current" if action == "publish" else "withdrawn"
            c.execute(
                "UPDATE moderation_policy_states SET status=?,version=version+1,reason=?,published_at=CASE WHEN ?='current' THEN ? ELSE published_at END,updated_by=?,updated_at=? WHERE policy_id=?",
                (status_name, reason, status_name, at, user["id"], at, policy_id),
            )
            audit_entry(
                c,
                user,
                "moderation.policy_" + action,
                "moderation_policy",
                policy_id,
                {
                    "reason": reason,
                    "version": version + 1,
                    "previousCurrentId": current[0] if current else None,
                },
            )
            status, result = remember(
                c,
                user,
                key,
                hashed,
                "policy.change",
                policy_id,
                {"id": policy_id, "version": version + 1, "status": status_name},
                policy_id=policy_id,
            )
            return status, {
                **result,
                "policy": self.view(self.get(c, policy_id), True),
            }
