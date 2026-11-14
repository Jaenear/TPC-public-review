"""Owned, versioned personal boundaries for community conversations."""

import json
import re
from ..core import ApiError, new_id, now_iso
from ..community_records import (
    canonical,
    digest,
    expected,
    fields,
    identifier,
    request_key,
)
from .common import audit
from .moderation_shared import actor, limit


def page_number(query, allowed=("page",)):
    if set(query) - set(allowed) or any(len(v) != 1 for v in query.values()):
        raise ApiError(400, "목록 조회 항목을 확인해주세요.")
    raw = query.get("page", ["1"])[0]
    if not isinstance(raw, str) or not re.fullmatch(r"[1-9][0-9]{0,4}", raw):
        raise ApiError(400, "페이지 번호를 확인해주세요.")
    page = int(raw)
    if page > 10000:
        raise ApiError(400, "페이지 번호를 확인해주세요.")
    return page


class CommunityBlockService:
    def __init__(self, app):
        self.app = app

    def target(self, c, user, target_id):
        identifier(target_id)
        if target_id == user["id"]:
            raise ApiError(400, "본인을 차단할 수 없습니다.")
        row = c.execute("SELECT id,name FROM users WHERE id=?", (target_id,)).fetchone()
        if not row:
            raise ApiError(404, "회원을 찾을 수 없습니다.")
        return row

    def current(self, c, user, target_id):
        target = self.target(c, user, target_id)
        row = c.execute(
            "SELECT * FROM community_block_heads WHERE user_id=? AND target_id=?",
            (user["id"], target_id),
        ).fetchone()
        return {
            "targetId": target_id,
            "targetName": target["name"],
            "blocked": bool(row["blocked"]) if row else False,
            "version": row["version"] if row else 0,
            "origin": "member" if row else "no_intent",
            "updatedAt": row["updated_at"] if row else None,
        }

    def read(self, user, target_id, query):
        if query:
            raise ApiError(400, "본인의 대화 차단만 조회할 수 있습니다.")
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user = actor(c, user)
            return {"block": self.current(c, user, target_id)}

    def listing(self, user, query):
        page = page_number(query)
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user = actor(c, user)
            total = c.execute(
                "SELECT COUNT(*) FROM community_block_heads WHERE user_id=? AND blocked=1",
                (user["id"],),
            ).fetchone()[0]
            rows = c.execute(
                "SELECT target_id FROM community_block_heads WHERE user_id=? AND blocked=1 ORDER BY updated_at DESC,target_id LIMIT 20 OFFSET ?",
                (user["id"], (page - 1) * 20),
            ).fetchall()
            return {
                "blocks": [self.current(c, user, r[0]) for r in rows],
                "page": page,
                "pageSize": 20,
                "total": total,
                "scope": "own_community_conversations",
            }

    def write(self, user, target_id, data):
        fields(
            data,
            ("blocked", "expectedVersion", "requestKey"),
            ("blocked", "expectedVersion", "requestKey"),
        )
        if type(data["blocked"]) is not bool:
            raise ApiError(400, "차단 여부를 확인해주세요.")
        version = expected(data["expectedVersion"], zero=True)
        key = request_key(data["requestKey"])
        identifier(target_id)
        hashed = digest(
            {
                "operation": "community.block",
                "targetId": target_id,
                "data": {k: v for k, v in data.items() if k != "requestKey"},
            }
        )
        limit(self.app, user, "community-block")
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = actor(c, user)
            current = self.current(c, user, target_id)
            old = c.execute(
                "SELECT request_hash,result FROM community_block_operations WHERE user_id=? AND request_key=?",
                (user["id"], key),
            ).fetchone()
            if old:
                if old["request_hash"] != hashed:
                    raise ApiError(
                        409, "같은 요청 식별자로 다른 차단 처리를 할 수 없습니다."
                    )
                return {
                    "receipt": json.loads(old["result"]),
                    "currentBlock": current,
                    "replayed": True,
                }
            if version != current["version"]:
                raise ApiError(
                    409, "차단 설정이 변경되었습니다. 최신 상태를 확인해주세요."
                )
            previous = c.execute(
                "SELECT current_operation_id FROM community_block_heads WHERE user_id=? AND target_id=?",
                (user["id"], target_id),
            ).fetchone()
            oid, timestamp = new_id("communityblock"), now_iso()
            receipt = {
                "id": oid,
                "targetId": target_id,
                "version": version + 1,
                "blocked": data["blocked"],
                "requestKey": key,
                "recordedAt": timestamp,
            }
            if previous:
                c.execute(
                    "UPDATE community_block_heads SET blocked=?,version=?,current_operation_id=?,updated_at=? WHERE user_id=? AND target_id=?",
                    (
                        int(data["blocked"]),
                        version + 1,
                        oid,
                        timestamp,
                        user["id"],
                        target_id,
                    ),
                )
            else:
                c.execute(
                    "INSERT INTO community_block_heads(user_id,target_id,blocked,version,current_operation_id,updated_at) VALUES(?,?,?,?,?,?)",
                    (user["id"], target_id, int(data["blocked"]), 1, oid, timestamp),
                )
            c.execute(
                "INSERT INTO community_block_operations(id,user_id,target_id,request_key,request_hash,expected_version,result_version,before_blocked,after_blocked,previous_operation_id,result,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    oid,
                    user["id"],
                    target_id,
                    key,
                    hashed,
                    version,
                    version + 1,
                    int(current["blocked"]),
                    int(data["blocked"]),
                    previous[0] if previous else None,
                    canonical(receipt),
                    timestamp,
                ),
            )
            audit(
                c,
                user,
                "community.block.changed",
                "user",
                target_id,
                {
                    "operationId": oid,
                    "version": version + 1,
                    "blocked": data["blocked"],
                },
            )
            return {
                "receipt": receipt,
                "currentBlock": self.current(c, user, target_id),
                "replayed": False,
            }

    def route(self, h, path, method, query):
        if path == "/api/me/community-blocks":
            if method != "GET":
                raise ApiError(405, "차단 대상과 기준 판본을 명시해주세요.")
            return 200, self.listing(h.user, query), {}
        match = re.fullmatch(r"/api/me/community-blocks/([A-Za-z0-9_-]+)", path)
        if not match:
            return None
        if method == "GET":
            result = self.read(h.user, match.group(1), query)
        elif method == "PATCH":
            if query:
                raise ApiError(400, "차단 요청 경로를 확인해주세요.")
            result = self.write(h.user, match.group(1), h.body())
        else:
            raise ApiError(405, "지원하지 않는 차단 요청입니다.")
        return 200, result, {}
