"""Own preferences, permission-aware inbox and bounded idempotent read receipts."""

import base64
import hashlib
import hmac
import json
import re
import time
from ..core import ApiError, new_id, now_iso
from ..notification_records import (
    CATEGORIES,
    CHANNELS,
    canonical,
    digest,
    preference,
    reference_from_href,
)
from .learning_start import active_member
from .contracts import managed as managed_contract


class NotificationService:
    scope_ttl = 3600

    def __init__(self, app):
        self.app = app
        self.clock = time.time

    def actor(self, c, user):
        current = active_member(c, user)
        if not current:
            raise ApiError(401, "로그인 후 본인 알림을 확인해주세요.")
        return current

    def fields(self, data, allowed, required):
        if (
            not isinstance(data, dict)
            or set(data) - set(allowed)
            or set(required) - set(data)
        ):
            raise ApiError(400, "알림 요청 항목을 확인해주세요.")

    def request_key(self, value):
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{12,120}", value
        ):
            raise ApiError(400, "알림 저장 요청 식별자를 확인해주세요.")
        return value

    def saved(self, c, user, key, hashed):
        row = c.execute(
            "SELECT * FROM notification_operations WHERE user_id=? AND request_key=?",
            (user["id"], key),
        ).fetchone()
        if row and row["request_hash"] != hashed:
            raise ApiError(409, "같은 요청 식별자로 다른 알림 처리를 할 수 없습니다.")
        return row

    def preferences_in(self, c, user):
        return {
            "preferences": [
                preference(c, user["id"], category, channel)
                for category in CATEGORIES
                for channel in CHANNELS
            ],
            "deliveryPolicy": {
                "basis": "product_defaults_pending_operating_acceptance",
                "sitePreferenceIsEmailConsent": False,
                "marketingConsentGranted": False,
                "emailConfigured": False,
                "marketingConfigured": False,
                "requiredTypesApproved": False,
            },
        }

    def preferences(self, user):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            return self.preferences_in(c, self.actor(c, user))

    def change_preference(self, user, data):
        self.fields(
            data,
            {"category", "channel", "enabled", "expectedVersion", "requestKey"},
            {"category", "channel", "enabled", "expectedVersion", "requestKey"},
        )
        category, channel = data["category"], data["channel"]
        if (
            category not in CATEGORIES
            or channel not in CHANNELS
            or type(data["enabled"]) is not bool
            or type(data["expectedVersion"]) is not int
            or not 0 <= data["expectedVersion"] < 2147483647
        ):
            raise ApiError(400, "알림 유형·채널·선호 기준 버전을 확인해주세요.")
        key = self.request_key(data["requestKey"])
        hashed = digest({"operation": "preference", "data": data})
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = self.actor(c, user)
            old = self.saved(c, user, key, hashed)
            if old:
                return {
                    "receipt": json.loads(old["result"]),
                    "currentPreference": preference(c, user["id"], category, channel),
                    **self.preferences_in(c, user),
                }
            before = preference(c, user["id"], category, channel)
            if before["version"] != data["expectedVersion"]:
                raise ApiError(
                    409, "알림 선호가 변경되었습니다. 현재 설정을 확인해주세요."
                )
            op, at, version = (
                new_id("noticeoperation"),
                now_iso(),
                before["version"] + 1,
            )
            if before["version"]:
                c.execute(
                    "UPDATE notification_preferences SET enabled=?,version=?,current_operation_id=?,updated_at=? WHERE user_id=? AND category=? AND channel=? AND version=?",
                    (
                        int(data["enabled"]),
                        version,
                        op,
                        at,
                        user["id"],
                        category,
                        channel,
                        before["version"],
                    ),
                )
            else:
                c.execute(
                    "INSERT INTO notification_preferences(user_id,category,channel,enabled,version,current_operation_id,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        user["id"],
                        category,
                        channel,
                        int(data["enabled"]),
                        version,
                        op,
                        at,
                    ),
                )
            after = preference(c, user["id"], category, channel)
            receipt = {
                "id": op,
                "operation": "preference",
                "category": category,
                "channel": channel,
                "version": version,
                "enabled": data["enabled"],
                "requestKey": key,
            }
            c.execute(
                "INSERT INTO notification_operations(id,user_id,request_key,request_hash,kind,category,channel,expected_version,result_version,result_enabled,before_state,after_state,result,created_at) VALUES(?,?,?,?,'preference',?,?,?,?,?,?,?,?,?)",
                (
                    op,
                    user["id"],
                    key,
                    hashed,
                    category,
                    channel,
                    before["version"],
                    version,
                    int(data["enabled"]),
                    canonical(before),
                    canonical(after),
                    canonical(receipt),
                    at,
                ),
            )
            return {
                "receipt": receipt,
                "currentPreference": after,
                **self.preferences_in(c, user),
            }

    def filters(self, query):
        allowed = {"category", "state", "page", "limit", "groupBy", "groupId"}
        if set(query) - allowed or any(
            not isinstance(values, list) or len(values) != 1
            for values in query.values()
        ):
            raise ApiError(400, "알림 목록 조건을 확인해주세요.")
        category, state = (
            query.get("category", [None])[0],
            query.get("state", ["all"])[0],
        )
        group_id, grouping = (
            query.get("groupId", [None])[0],
            query.get("groupBy", ["none"])[0],
        )
        try:
            page, limit = int(query.get("page", ["1"])[0]), int(
                query.get("limit", ["100"])[0]
            )
        except (TypeError, ValueError):
            raise ApiError(400, "알림 페이지를 확인해주세요.")
        if (
            (category is not None and category not in CATEGORIES)
            or state not in ("all", "read", "unread")
            or grouping not in ("none", "reference")
            or not 1 <= page <= 10000
            or limit not in (20, 50, 100)
            or (
                group_id is not None
                and (not isinstance(group_id, str) or not 1 <= len(group_id) <= 180)
            )
        ):
            raise ApiError(400, "알림 유형·읽음·묶음 조건을 확인해주세요.")
        return (
            {"category": category, "state": state, "groupId": group_id},
            page,
            limit,
            grouping,
        )

    def selection(self, c, user, filters, upper=None):
        where = [
            "n.user_id=?",
            "i.recipient_id=n.user_id",
            "i.channel='site'",
            "i.decision='site_created'",
        ]
        args = [user["id"]]
        if filters["category"] is not None:
            where.append("i.category=?")
            args.append(filters["category"])
        if filters["state"] != "all":
            where.append(
                "n.read_at IS "
                + ("NULL" if filters["state"] == "unread" else "NOT NULL")
            )
        if filters["groupId"]:
            anchor = c.execute(
                "SELECT * FROM notification_intents WHERE id=? AND recipient_id=? AND channel='site'",
                (filters["groupId"], user["id"]),
            ).fetchone()
            if not anchor:
                raise ApiError(404, "본인 알림 묶음을 찾을 수 없습니다.")
            if anchor["group_key"] is None:
                where.append("i.id=?")
                args.append(anchor["id"])
            else:
                where.extend(["i.category=?", "i.group_key=?"])
                args.extend([anchor["category"], anchor["group_key"]])
        if upper is not None:
            where.append("i.seq<=?")
            args.append(upper)
        return (
            " FROM notifications n JOIN notification_intents i ON i.notification_id=n.id WHERE "
            + " AND ".join(where),
            args,
        )

    def signing_key(self, c):
        key = c.execute(
            "SELECT secret FROM notification_signing_keys WHERE id='v1'"
        ).fetchone()
        if not key:
            raise ApiError(409, "알림 읽음 범위를 준비하지 못했습니다.")
        return bytes(key[0])

    def scope(self, c, user, filters):
        upper = c.execute(
            "SELECT id FROM notification_intents WHERE recipient_id=? AND channel='site' AND decision='site_created' ORDER BY seq DESC LIMIT 1",
            (user["id"],),
        ).fetchone()
        payload = {
            "version": 1,
            "ownerId": user["id"],
            "filters": filters,
            "upperId": upper[0] if upper else None,
            "issuedAt": int(self.clock()),
        }
        encode = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
        message = encode(canonical(payload).encode())
        signed = encode(
            hmac.new(
                self.signing_key(c),
                ("notification-read-v1:" + message).encode(),
                hashlib.sha256,
            ).digest()
        )
        return "v1." + message + "." + signed

    def decode_scope(self, c, user, token, expired=False):
        if (
            not isinstance(token, str)
            or len(token) > 3000
            or not re.fullmatch(r"v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}", token)
        ):
            raise ApiError(400, "알림 목록의 읽음 범위를 확인해주세요.")
        _, message, signed = token.split(".")
        actual = (
            base64.urlsafe_b64encode(
                hmac.new(
                    self.signing_key(c),
                    ("notification-read-v1:" + message).encode(),
                    hashlib.sha256,
                ).digest()
            )
            .decode()
            .rstrip("=")
        )
        if not hmac.compare_digest(actual, signed):
            raise ApiError(400, "알림 읽음 범위가 변경되었습니다.")
        try:
            packet = json.loads(
                base64.urlsafe_b64decode(message + "=" * (-len(message) % 4))
            )
        except (ValueError, UnicodeError):
            raise ApiError(400, "알림 읽음 범위를 확인해주세요.")
        if (
            not isinstance(packet, dict)
            or set(packet) != {"version", "ownerId", "filters", "upperId", "issuedAt"}
            or type(packet["version"]) is not int
            or packet["version"] != 1
            or packet["ownerId"] != user["id"]
        ):
            raise ApiError(403, "본인 알림 목록에서 다시 확인해주세요.")
        filt = packet["filters"]
        if not isinstance(filt, dict) or set(filt) != {"category", "state", "groupId"}:
            raise ApiError(400, "알림 목록 조건을 확인해주세요.")
        query = {k: [v] for k, v in filt.items() if v is not None}
        self.filters(query)
        issued, current = packet["issuedAt"], int(self.clock())
        if (
            type(issued) is not int
            or issued > current + 5
            or (not expired and current - issued > self.scope_ttl)
        ):
            raise ApiError(409, "알림 목록을 다시 불러온 뒤 읽음 범위를 확인해주세요.")
        upper = 0
        if packet["upperId"] is not None:
            if not isinstance(packet["upperId"], str):
                raise ApiError(400, "알림 읽음 상한을 확인해주세요.")
            found = c.execute(
                "SELECT seq FROM notification_intents WHERE id=? AND recipient_id=?",
                (packet["upperId"], user["id"]),
            ).fetchone()
            if not found:
                raise ApiError(409, "알림 목록의 상한을 다시 확인해주세요.")
            upper = found[0]
        return packet, upper

    def available(self, c, user, item):
        kind, target = item["reference_kind"], item["reference_id"]
        platform = self.app.platform
        try:
            if kind == "question":
                platform.discussions.question(c, user, target)
            elif kind == "post":
                platform.discussions.post(c, user, target)
            elif kind == "comment":
                return platform.community_comments.reference_available(c, user, target)
            elif kind in ("moderation_action", "staff_action"):
                platform.moderation.action_scope(
                    c, user, target, kind == "staff_action"
                )
            elif kind in ("moderation_appeal", "staff_appeal"):
                platform.moderation.appeals.scope(
                    c, user, target, kind == "staff_appeal"
                )
            elif kind == "course_staff":
                platform.content.managed_course(c, user, target)
            elif kind == "revision":
                platform.revisions.revision(c, user, target)
            elif kind == "contract":
                managed_contract(c, user, target)
            elif kind in ("purchase", "enrollment", "booking", "report"):
                table = {
                    "purchase": "purchase_orders",
                    "enrollment": "enrollments",
                    "booking": "bookings",
                    "report": "reports",
                }[kind]
                if not c.execute(
                    "SELECT 1 FROM " + table + " WHERE id=? AND user_id=?",
                    (target, user["id"]),
                ).fetchone():
                    return False
            elif kind == "own":
                return target == user["id"]
            else:
                return False
            return True
        except ApiError as error:
            if error.status not in (401, 403, 404):
                raise
            return False

    def view(self, c, user, item):
        allowed = self.available(c, user, item)
        _, _, _, href = reference_from_href(item["href"], user["id"])
        opaque = not allowed or item["reference_kind"] == "unknown"
        historical_own = item["origin"] != "event" and item["reference_kind"] == "own"
        return {
            "id": item["id"],
            "userId": user["id"],
            "readAt": item["read_at"],
            "createdAt": item["created_at"],
            "title": (
                "현재 확인할 수 없는 알림입니다"
                if opaque
                else (
                    "내 계정의 이전 알림이 있습니다"
                    if historical_own
                    else item["title"]
                )
            ),
            "body": (
                "참조 항목의 현재 접근 권한이나 상태를 확인해주세요."
                if opaque
                else (
                    "현재 내 계정의 내역에서 다시 확인해주세요."
                    if historical_own
                    else item["body"]
                )
            ),
            "href": "#/inbox" if opaque else href,
            "category": item["category"],
            "referenceAvailable": allowed,
            "reference": (
                {"kind": item["reference_kind"], "id": item["reference_id"]}
                if allowed
                else None
            ),
        }

    def listing_in(self, c, user, query):
        filters, page, limit, grouping = self.filters(query)
        selection, args = self.selection(c, user, filters)
        select = "SELECT n.*,i.id AS intent_id,i.category,i.origin,i.reference_kind,i.reference_id,i.group_key,i.seq"
        total = c.execute("SELECT COUNT(*)" + selection, args).fetchone()[0]
        filtered_unread = c.execute(
            "SELECT COUNT(*)" + selection + " AND n.read_at IS NULL", args
        ).fetchone()[0]
        unread = c.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL",
            (user["id"],),
        ).fetchone()[0]
        groups = []
        if grouping == "reference":
            grouped = (
                "SELECT i.category,i.group_key,CASE WHEN i.group_key IS NULL THEN i.id ELSE NULL END AS singleton,MAX(i.seq) AS latest_seq,COUNT(*) AS total,SUM(n.read_at IS NULL) AS unread"
                + selection
                + " GROUP BY i.category,i.group_key,singleton"
            )
            group_total = c.execute(
                "SELECT COUNT(*) FROM (" + grouped + ")", args
            ).fetchone()[0]
            buckets = c.execute(
                grouped + " ORDER BY latest_seq DESC LIMIT ? OFFSET ?",
                args + [limit, (page - 1) * limit],
            ).fetchall()
            rows = []
            for bucket in buckets:
                latest = c.execute(
                    select
                    + " FROM notifications n JOIN notification_intents i ON i.notification_id=n.id WHERE i.seq=? AND n.user_id=?",
                    (bucket["latest_seq"], user["id"]),
                ).fetchone()
                if bucket["group_key"] is None:
                    anchor = bucket["singleton"]
                else:
                    anchor = c.execute(
                        "SELECT id FROM notification_intents WHERE recipient_id=? AND category=? AND channel='site' AND group_key=? ORDER BY seq LIMIT 1",
                        (user["id"], bucket["category"], bucket["group_key"]),
                    ).fetchone()[0]
                dto = self.view(c, user, latest)
                groups.append(
                    {
                        "id": anchor,
                        "category": bucket["category"],
                        "notificationCount": bucket["total"],
                        "unreadCount": bucket["unread"],
                        "latest": dto,
                    }
                )
                rows.append(latest)
        else:
            rows = c.execute(
                select + selection + " ORDER BY i.seq DESC LIMIT ? OFFSET ?",
                args + [limit, (page - 1) * limit],
            ).fetchall()
            group_total = None
        return {
            "notifications": [self.view(c, user, row) for row in rows],
            "unreadCount": unread,
            "filteredUnreadCount": filtered_unread,
            "total": total,
            "page": page,
            "pageSize": limit,
            "groupBy": grouping,
            "groups": groups,
            "groupTotal": group_total,
            "filter": filters,
            "readScope": self.scope(c, user, filters),
            "scopeLifetimeSeconds": self.scope_ttl,
        }

    def listing(self, user, query):
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            return self.listing_in(c, self.actor(c, user), query)

    def read(self, user, data):
        self.fields(data, {"id", "all", "readScope", "requestKey"}, set())
        bulk = data.get("all") is True
        if bulk:
            if "readScope" not in data:
                raise ApiError(
                    409, "알림 목록을 다시 불러온 뒤 읽음 범위를 확인해주세요."
                )
            self.fields(
                data,
                {"all", "readScope", "requestKey"},
                {"all", "readScope", "requestKey"},
            )
            key = self.request_key(data["requestKey"])
        else:
            self.fields(data, {"id", "requestKey"}, {"id"})
            if not isinstance(data["id"], str) or not 1 <= len(data["id"]) <= 150:
                raise ApiError(400, "알림 식별자를 확인해주세요.")
            key = (
                self.request_key(data["requestKey"])
                if "requestKey" in data
                else "legacy-read/" + data["id"]
            )
        hashed = digest({"operation": "read", "data": data})
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            user = self.actor(c, user)
            old = self.saved(c, user, key, hashed)
            if old:
                scope = json.loads(old["scope"])
                filters = scope.get("filters", {})
                return {
                    "receipt": json.loads(old["result"]),
                    **self.listing_in(
                        c, user, {k: [v] for k, v in filters.items() if v is not None}
                    ),
                }
            if bulk:
                packet, upper = self.decode_scope(c, user, data["readScope"])
                selection, args = self.selection(c, user, packet["filters"], upper)
                ids = [
                    r[0]
                    for r in c.execute(
                        "SELECT n.id"
                        + selection
                        + " AND n.read_at IS NULL ORDER BY i.seq",
                        args,
                    )
                ]
                scope = packet
                filters = packet["filters"]
            else:
                row = c.execute(
                    "SELECT read_at FROM notifications WHERE id=? AND user_id=?",
                    (data["id"], user["id"]),
                ).fetchone()
                if not row:
                    raise ApiError(404, "본인 알림을 찾을 수 없습니다.")
                ids = [data["id"]] if row["read_at"] is None else []
                scope = {"singleId": data["id"]}
                filters = {}
            at, op = now_iso(), new_id("noticeoperation")
            c.executemany(
                "UPDATE notifications SET read_at=? WHERE id=? AND user_id=? AND read_at IS NULL",
                [(at, nid, user["id"]) for nid in ids],
            )
            receipt = {
                "id": op,
                "operation": "read",
                "requestKey": key,
                "changedCount": len(ids),
                "readAt": at,
                "scope": scope,
            }
            c.execute(
                "INSERT INTO notification_operations(id,user_id,request_key,request_hash,kind,scope,changed_count,affected_hash,read_at,result,created_at) VALUES(?,?,?,?,'read',?,?,?,?,?,?)",
                (
                    op,
                    user["id"],
                    key,
                    hashed,
                    canonical(scope),
                    len(ids),
                    digest(ids),
                    at,
                    canonical(receipt),
                    at,
                ),
            )
            return {
                "receipt": receipt,
                **self.listing_in(
                    c, user, {k: [v] for k, v in filters.items() if v is not None}
                ),
            }

    def operation(self, user, key, query):
        """An owned historical result, without replaying a write or old summary."""
        if query:
            raise ApiError(400, "본인의 알림 저장 결과만 확인할 수 있습니다.")
        self.request_key(key)
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            user = self.actor(c, user)
            row = c.execute(
                "SELECT kind,category,channel,result FROM notification_operations WHERE user_id=? AND request_key=?",
                (user["id"], key),
            ).fetchone()
            if not row:
                raise ApiError(404, "본인의 알림 저장 결과를 찾을 수 없습니다.")
            result = {"receipt": json.loads(row["result"]), "replayed": True}
            if row["kind"] == "preference":
                result.update(
                    currentPreference=preference(
                        c, user["id"], row["category"], row["channel"]
                    ),
                    **self.preferences_in(c, user),
                )
            else:
                result["currentUnreadCount"] = c.execute(
                    "SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL",
                    (user["id"],),
                ).fetchone()[0]
            return result

    def route(self, h, path, method, query):
        operation = re.fullmatch(
            r"/api/me/notification-operations/([A-Za-z0-9_-]+)", path
        )
        if operation:
            if method != "GET":
                raise ApiError(405, "알림 저장 결과는 조회로만 확인합니다.")
            return 200, self.operation(h.user, operation.group(1), query), {}
        if path not in ("/api/notifications", "/api/me/notification-preferences"):
            return None
        if (
            method not in ("GET", "PATCH")
            or (method == "PATCH" and query)
            or (path.endswith("preferences") and query)
        ):
            raise ApiError(
                405 if method not in ("GET", "PATCH") else 400,
                "알림 요청 방식이나 조건을 확인해주세요.",
            )
        user = h.user
        if path.endswith("preferences"):
            result = (
                self.preferences(user)
                if method == "GET"
                else self.change_preference(user, h.body())
            )
        else:
            result = (
                self.listing(user, query)
                if method == "GET"
                else self.read(user, h.body())
            )
        return 200, result, {}
