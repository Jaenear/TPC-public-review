"""One-level replies, immutable content revisions and atomic conversation events."""

import json
import re
from ..core import ApiError, new_id, now_iso, text_field
from ..community_records import (
    blocked_pair,
    body_hash,
    canonical,
    digest,
    expected,
    fields,
    identifier,
    request_key,
)
from ..moderation_records import initialize_comment
from .common import audit, notify, row_dict
from .learning_start import active_member
from .moderation_shared import actor, limit
from .community_blocks import page_number

COMMENT_ROWS = "SELECT cm.*,u.name AS author,h.parent_id,h.content_version,h.current_revision_id,h.origin AS content_origin,COALESCE(mh.owner_deleted,0) AS owner_deleted,COALESCE(mh.version,1) AS moderation_version,r.body AS revision_body,r.body_hash AS revision_hash,r.event_at AS revision_at,r.origin AS revision_origin FROM comments cm JOIN users u ON u.id=cm.user_id JOIN community_comment_heads h ON h.comment_id=cm.id LEFT JOIN moderation_comment_heads mh ON mh.comment_id=cm.id LEFT JOIN community_comment_revisions r ON r.id=h.current_revision_id"


class CommunityCommentService:
    def __init__(self, app, discussions):
        self.app, self.discussions = app, discussions

    def row(self, c, comment_id):
        row = c.execute(
            COMMENT_ROWS + " WHERE cm.id=?",
            (comment_id,),
        ).fetchone()
        if not row:
            raise ApiError(404, "댓글을 찾을 수 없습니다.")
        return row

    def published(self, row):
        return row["status"] == "published" and not row["owner_deleted"]

    def consistent(self, row):
        return row["current_revision_id"] is None or (
            row["revision_body"] == row["body"]
            and row["revision_hash"] == body_hash(row["body"])
        )

    def visible(self, c, user, row):
        return self.published(row) and not blocked_pair(
            c, user["id"] if user else None, row["user_id"]
        )

    def writing_post(self, c, user, post_id):
        post = self.discussions.post(c, user, post_id)
        if post["locked"] or post["status"] != "published":
            raise ApiError(409, "댓글을 작성하거나 수정할 수 없는 게시글입니다.")
        if blocked_pair(c, user["id"], post["user_id"]):
            raise ApiError(403, "현재 이 대화에 참여할 수 없습니다.")
        return post

    def parent(self, c, user, post_id, parent_id, parent_version=None):
        if parent_id is None:
            return None
        parent = self.row(c, parent_id)
        if parent["post_id"] != post_id:
            raise ApiError(400, "같은 게시글의 댓글에 답글을 남겨주세요.")
        if not self.published(parent) or parent["parent_id"] is not None:
            raise ApiError(409, "답글은 공개된 원댓글에 한 단계까지 남길 수 있습니다.")
        if blocked_pair(c, user["id"], parent["user_id"]):
            raise ApiError(403, "현재 이 대화에 참여할 수 없습니다.")
        if not self.consistent(parent):
            raise ApiError(
                409, "원댓글 본문이 변경되었습니다. 최신 상태를 확인해주세요."
            )
        if parent_version is not None and parent_version != parent["content_version"]:
            raise ApiError(
                409, "원댓글 판본이 변경되었습니다. 내용을 다시 확인해주세요."
            )
        return parent

    def dto(self, c, user, row, post, context=None):
        consistent = self.consistent(row)
        blocked = context["blocked"] if context is not None else None

        def boundary(target):
            return (
                target in blocked
                if blocked is not None
                else blocked_pair(c, user["id"] if user else None, target)
            )

        writable = bool(user and post["status"] == "published" and not post["locked"])
        if writable:
            writable = not boundary(post["user_id"])
        parent_ok = True
        if row["parent_id"]:
            parent = (
                context["parents"][row["parent_id"]]
                if context is not None
                else self.row(c, row["parent_id"])
            )
            parent_ok = (
                self.published(parent)
                and self.consistent(parent)
                and not boundary(parent["user_id"])
            )
        item = row_dict(row)
        return {
            **{
                k: item[k]
                for k in (
                    "id",
                    "postId",
                    "userId",
                    "body",
                    "status",
                    "createdAt",
                    "author",
                )
            },
            "parentId": row["parent_id"],
            "rootId": row["parent_id"] or row["id"],
            "contentVersion": row["content_version"] if consistent else None,
            "contentOrigin": row["content_origin"],
            "contentState": (
                "observed"
                if row["current_revision_id"] is None
                else "verified" if consistent else "unverified"
            ),
            "bodyHash": body_hash(row["body"]),
            "editedAt": (
                row["revision_at"] if row["revision_origin"] == "edited" else None
            ),
            "moderationVersion": row["moderation_version"],
            "tombstone": False,
            "canEdit": bool(
                writable and parent_ok and consistent and user["id"] == row["user_id"]
            ),
            "canViewHistory": bool(
                user
                and user["id"] == row["user_id"]
                and consistent
                and self.published(row)
                and not boundary(row["user_id"])
            ),
            "canReply": bool(
                writable
                and consistent
                and row["parent_id"] is None
                and self.published(row)
                and not boundary(row["user_id"])
            ),
        }

    def listing(self, c, user, post):
        # Keep legacy flat-list compatibility without adding one SQL query per
        # comment. Unavailable parents remain internal and never enter a DTO.
        all_rows = c.execute(
            COMMENT_ROWS + " WHERE cm.post_id=? ORDER BY cm.created_at,cm.id",
            (post["id"],),
        ).fetchall()
        blocked = set()
        if user:
            blocked = {
                r[0] if r[1] == user["id"] else r[1]
                for r in c.execute(
                    "SELECT target_id,user_id FROM community_block_heads WHERE blocked=1 AND (user_id=? OR target_id=?)",
                    (user["id"], user["id"]),
                )
            }
        context = {"blocked": blocked, "parents": {r["id"]: r for r in all_rows}}
        rows = [
            r for r in all_rows if self.published(r) and r["user_id"] not in blocked
        ]
        included = {r["id"] for r in rows}
        result, placeholders = [], set()
        for row in rows:
            parent_id = row["parent_id"]
            if (
                parent_id
                and parent_id not in included
                and parent_id not in placeholders
            ):
                # Preserve a child's relationship without exposing the unavailable
                # parent's body, author, history or reason for unavailability.
                result.append(
                    {
                        "id": parent_id,
                        "postId": post["id"],
                        "parentId": None,
                        "rootId": parent_id,
                        "tombstone": True,
                        "body": None,
                        "author": None,
                        "userId": None,
                        "contentVersion": None,
                        "canEdit": False,
                        "canReply": False,
                    }
                )
                placeholders.add(parent_id)
            result.append(self.dto(c, user, row, post, context))
        return result

    def scoped(self, c, user, comment_id):
        row = self.row(c, comment_id)
        post = self.discussions.post(c, user, row["post_id"])
        if not self.visible(c, user, row):
            raise ApiError(404, "댓글을 찾을 수 없습니다.")
        return row, post

    def reference_available(self, c, user, comment_id):
        self.scoped(c, user, comment_id)
        return True

    def read(self, user, comment_id, query):
        if query:
            raise ApiError(400, "댓글 조회 항목을 확인해주세요.")
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user = active_member(c, user)
            row, post = self.scoped(c, user, comment_id)
            return {"comment": self.dto(c, user, row, post)}

    def operation(self, user, key, query):
        """Read an owned receipt without replaying a now-forbidden old write.

        A receipt proves the historical save, not current body access. Deleted
        content never returns through this reconciliation path.
        """
        if query:
            raise ApiError(400, "저장 결과 조회 항목을 확인해주세요.")
        request_key(key)
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user = actor(c, user)
            op = c.execute(
                "SELECT result,comment_id,post_id FROM community_comment_operations WHERE user_id=? AND request_key=?",
                (user["id"], key),
            ).fetchone()
            if not op:
                raise ApiError(404, "본인의 저장 영수증을 찾을 수 없습니다.")
            post = self.discussions.post(c, user, op["post_id"])
            row = self.row(c, op["comment_id"])
            available = self.visible(c, user, row)
            return {
                "receipt": json.loads(op["result"]),
                "comment": self.dto(c, user, row, post) if available else None,
                "currentAvailable": available,
                "replayed": True,
            }

    def request(self, data, kind, target, legacy=False):
        key = (
            request_key(data["requestKey"])
            if "requestKey" in data
            else "legacy-create/" + new_id("request") if legacy else request_key(None)
        )
        hashed = digest(
            {
                "operation": "community.comment." + kind,
                "targetId": target,
                "data": {k: v for k, v in data.items() if k != "requestKey"},
            }
        )
        return key, hashed

    def replay(self, c, user, key, hashed):
        found = c.execute(
            "SELECT request_hash,result FROM community_comment_operations WHERE user_id=? AND request_key=?",
            (user["id"], key),
        ).fetchone()
        if found and found["request_hash"] != hashed:
            raise ApiError(409, "같은 요청 식별자로 다른 댓글을 저장할 수 없습니다.")
        return json.loads(found["result"]) if found else None

    def response(self, c, user, receipt, replayed):
        # Replay returns the original immutable receipt and the currently visible
        # state, never an old body from that receipt's former revision.
        row, post = self.scoped(c, user, receipt["commentId"])
        return {
            **self.discussions.post_detail_in(c, user, post),
            "receipt": receipt,
            "comment": self.dto(c, user, row, post),
            "replayed": replayed,
        }

    def record(
        self,
        c,
        user,
        key,
        hashed,
        kind,
        row,
        revision_id,
        timestamp,
        expected_version=None,
        parent_version=None,
    ):
        oid = new_id("commentop")
        receipt = {
            "id": oid,
            "kind": kind,
            "commentId": row["id"],
            "postId": row["post_id"],
            "parentId": row["parent_id"],
            "contentVersion": row["content_version"],
            "revisionId": revision_id,
            "requestKey": key,
            "recordedAt": timestamp,
        }
        if parent_version is not None:
            receipt["parentContentVersion"] = parent_version
        c.execute(
            "INSERT INTO community_comment_operations(id,user_id,request_key,request_hash,kind,comment_id,post_id,expected_version,result_version,revision_id,result,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                oid,
                user["id"],
                key,
                hashed,
                kind,
                row["id"],
                row["post_id"],
                expected_version,
                row["content_version"],
                revision_id,
                canonical(receipt),
                timestamp,
            ),
        )
        return receipt

    def revision(
        self,
        c,
        rid,
        cid,
        version,
        origin,
        body,
        timestamp,
        user=None,
        previous=None,
        reason=None,
    ):
        c.execute(
            "INSERT INTO community_comment_revisions(id,comment_id,version,origin,previous_revision_id,actor_id,body,body_hash,reason,event_at,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                cid,
                version,
                origin,
                previous,
                user["id"] if user else None,
                body,
                body_hash(body),
                reason,
                timestamp if user else None,
                timestamp,
            ),
        )

    def notify_participants(self, c, user, post, row, parent, event_key, edited=False):
        targets = {post["user_id"]}
        if parent is not None:
            targets.add(parent["user_id"])
        for target in sorted(targets - {user["id"]}):
            if (
                blocked_pair(c, user["id"], target)
                or not c.execute(
                    "SELECT 1 FROM users WHERE id=? AND status='active'", (target,)
                ).fetchone()
            ):
                continue
            notify(
                c,
                target,
                (
                    "참여한 대화의 댓글이 수정되었습니다"
                    if edited
                    else (
                        "댓글에 새 답글이 있습니다"
                        if parent is not None
                        else "게시글에 새 댓글이 있습니다"
                    )
                ),
                post["title"],
                "#/community/post/" + post["id"],
                event_key=event_key,
                category="conversation",
                reference_kind="comment",
                reference_id=row["id"],
                group_key="post:" + post["id"],
            )

    def create(self, user, post_id, data):
        fields(data, ("body", "parentId", "parentVersion", "requestKey"), ("body",))
        body = text_field(data, "body", minimum=2, maximum=5000)
        parent_id = data.get("parentId")
        if parent_id is not None:
            identifier(parent_id)
        if "parentVersion" in data and parent_id is None:
            raise ApiError(400, "원댓글을 함께 지정해주세요.")
        parent_version = (
            expected(data["parentVersion"]) if "parentVersion" in data else None
        )
        key, hashed = self.request(data, "create", post_id, legacy=parent_id is None)
        limit(self.app, user, "comment")
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = actor(c, user)
            post = self.writing_post(c, user, post_id)
            parent = self.parent(c, user, post_id, parent_id, parent_version)
            old = self.replay(c, user, key, hashed)
            if old:
                return self.response(c, user, old, True)
            cid, rid, timestamp = new_id("comment"), new_id("commentrev"), now_iso()
            c.execute(
                "INSERT INTO community_comment_heads(comment_id,post_id,parent_id,content_version,current_revision_id,origin,observed_at) VALUES(?,?,?,1,?,'created',?)",
                (cid, post_id, parent_id, rid, timestamp),
            )
            c.execute(
                "INSERT INTO comments(id,post_id,user_id,body,created_at) VALUES(?,?,?,?,?)",
                (cid, post_id, user["id"], body, timestamp),
            )
            initialize_comment(c, cid)
            self.revision(c, rid, cid, 1, "created", body, timestamp, user)
            row = self.row(c, cid)
            receipt = self.record(
                c,
                user,
                key,
                hashed,
                "create",
                row,
                rid,
                timestamp,
                parent_version=(
                    parent["content_version"] if parent is not None else None
                ),
            )
            audit(
                c,
                user,
                "community.comment.created",
                "comment",
                cid,
                {"revisionId": rid, "parentId": parent_id, "contentVersion": 1},
            )
            self.notify_participants(
                c, user, post, row, parent, "community.comment:" + cid
            )
            return self.response(c, user, receipt, False)

    def edit(self, user, comment_id, data):
        fields(
            data,
            ("body", "expectedVersion", "reason", "requestKey"),
            ("body", "expectedVersion", "reason", "requestKey"),
        )
        body = text_field(data, "body", minimum=2, maximum=5000)
        reason = text_field(data, "reason", minimum=3, maximum=2000)
        version = expected(data["expectedVersion"])
        key, hashed = self.request(data, "edit", comment_id)
        limit(self.app, user, "comment-edit")
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = actor(c, user)
            row = self.row(c, comment_id)
            post = self.writing_post(c, user, row["post_id"])
            if row["user_id"] != user["id"]:
                raise ApiError(403, "본인이 작성한 댓글만 수정할 수 있습니다.")
            if not self.published(row):
                raise ApiError(409, "공개된 본인 댓글만 수정할 수 있습니다.")
            parent = self.parent(c, user, row["post_id"], row["parent_id"])
            if not self.consistent(row):
                raise ApiError(
                    409, "댓글 본문과 판본이 다릅니다. 최신 상태를 확인해주세요."
                )
            old = self.replay(c, user, key, hashed)
            if old:
                return self.response(c, user, old, True)
            if version != row["content_version"]:
                raise ApiError(409, "댓글이 변경되었습니다. 최신 본문을 확인해주세요.")
            previous, rid, timestamp = (
                row["current_revision_id"],
                new_id("commentrev"),
                now_iso(),
            )
            if previous is None:
                previous = new_id("commentrev")
                self.revision(
                    c, previous, comment_id, 1, "baseline", row["body"], timestamp
                )
            c.execute(
                "UPDATE community_comment_heads SET content_version=content_version+1,current_revision_id=? WHERE comment_id=?",
                (rid, comment_id),
            )
            c.execute("UPDATE comments SET body=? WHERE id=?", (body, comment_id))
            c.execute(
                "UPDATE moderation_comment_heads SET version=version+1 WHERE comment_id=?",
                (comment_id,),
            )
            self.revision(
                c,
                rid,
                comment_id,
                version + 1,
                "edited",
                body,
                timestamp,
                user,
                previous,
                reason,
            )
            row = self.row(c, comment_id)
            receipt = self.record(
                c,
                user,
                key,
                hashed,
                "edit",
                row,
                rid,
                timestamp,
                expected_version=version,
            )
            audit(
                c,
                user,
                "community.comment.edited",
                "comment",
                comment_id,
                {"revisionId": rid, "contentVersion": version + 1},
            )
            self.notify_participants(
                c, user, post, row, parent, "community.comment.edit:" + rid, edited=True
            )
            return self.response(c, user, receipt, False)

    def history(self, user, comment_id, query):
        page = page_number(query, ("page", "through"))
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user = actor(c, user)
            row, _ = self.scoped(c, user, comment_id)
            if row["user_id"] != user["id"]:
                raise ApiError(404, "댓글 수정 이력을 찾을 수 없습니다.")
            if not self.consistent(row):
                raise ApiError(409, "댓글 판본을 다시 확인해주세요.")
            upper_id = query.get("through", [row["current_revision_id"]])[0]
            upper = 0
            if upper_id is not None:
                identifier(upper_id)
                anchor = c.execute(
                    "SELECT version FROM community_comment_revisions WHERE id=? AND comment_id=?",
                    (upper_id, comment_id),
                ).fetchone()
                if not anchor:
                    raise ApiError(404, "이 댓글의 수정 이력 상한을 찾을 수 없습니다.")
                upper = anchor[0]
            total = c.execute(
                "SELECT COUNT(*) FROM community_comment_revisions WHERE comment_id=? AND version<=?",
                (comment_id, upper),
            ).fetchone()[0]
            rows = c.execute(
                "SELECT * FROM community_comment_revisions WHERE comment_id=? AND version<=? ORDER BY version DESC LIMIT 20 OFFSET ?",
                (comment_id, upper, (page - 1) * 20),
            ).fetchall()
            return {
                "commentId": comment_id,
                "contentVersion": row["content_version"],
                "through": upper_id,
                "page": page,
                "pageSize": 20,
                "total": total,
                "revisions": [
                    {
                        "id": r["id"],
                        "version": r["version"],
                        "origin": r["origin"],
                        "previousRevisionId": r["previous_revision_id"],
                        "actorId": r["actor_id"],
                        "body": r["body"],
                        "bodyHash": r["body_hash"],
                        "reason": r["reason"],
                        "eventAt": r["event_at"],
                        "observedAt": r["observed_at"],
                    }
                    for r in rows
                ],
                "historicalEditsKnown": row["content_origin"] == "created",
            }

    def route(self, h, path, method, query):
        operation = re.fullmatch(
            r"/api/me/community-comment-operations/([A-Za-z0-9_-]+)", path
        )
        if operation:
            if method != "GET":
                raise ApiError(405, "저장 영수증은 조회로만 확인합니다.")
            return 200, self.operation(h.user, operation.group(1), query), {}
        match = re.fullmatch(
            r"/api/community/comments/([A-Za-z0-9_-]+)(/revisions)?", path
        )
        if not match:
            return None
        cid, revisions = match.groups()
        if revisions:
            if method != "GET":
                raise ApiError(405, "수정 이력은 조회로만 제공됩니다.")
            result = self.history(h.user, cid, query)
        elif method == "GET":
            result = self.read(h.user, cid, query)
        elif method == "PATCH":
            if query:
                raise ApiError(400, "댓글 수정 경로를 확인해주세요.")
            result = self.edit(h.user, cid, h.body())
        elif method == "DELETE":
            return None  # Preserve existing owner deletion and moderation paths.
        else:
            raise ApiError(405, "지원하지 않는 댓글 요청입니다.")
        return 200, result, {}
