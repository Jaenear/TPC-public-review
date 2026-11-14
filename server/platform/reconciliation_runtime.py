"""Configured ingress and leased PG query runtime; business changes are SQL-only."""

import json
import logging
import os
import re
import sqlite3
import threading

from ..core import ApiError, new_id, text_field
from .common import audit
from .commerce import GatewayError
from .order_products import active_actor
from .reconciliation_contract import TechnicalPolicy, checked_version, document, hashed, opaque
from .reconciliation_ingress import configured_scope, parse_event, read_raw, verify_signature
from .reconciliation_processor import PaymentQueryProcessor
from .reconciliation_query_work import QueryWork
from .reconciliation_queue import at, job_view

QUERY_POLICY = TechnicalPolicy(max_attempts=10)


class ReconciliationRuntime:
    def __init__(self, app, commerce, model):
        self.app, self.commerce, self.model = app, commerce, model
        self.clock = lambda: model.clock()
        self.work = QueryWork(app.db, self.clock)
        self.processor = PaymentQueryProcessor(app, commerce)
        self.stop_event = threading.Event()
        self.thread = None

    def scope(self):
        gateway = self.commerce.gateway
        scope = configured_scope(gateway)
        if scope and scope.mode == "live" and not self.app.secure_cookies:
            return None
        return scope

    def describe(self):
        gateway, scope = self.commerce.gateway, self.scope()
        supported = getattr(gateway, "can_lookup_order", None)
        return {"provider": "toss", "mode": gateway.mode, "merchantConfigured": scope is not None,
                "webhookConfigured": scope is not None and bool(getattr(gateway, "webhook_security_keys", ())),
                "paymentLookupSupported": callable(getattr(gateway, "lookup_payment", None)),
                "orderLookupSupported": bool(supported()) if callable(supported) else False,
                "workerRunning": bool(self.thread and self.thread.is_alive()),
                "technicalPolicy": {"version": 1, "maxAttempts": QUERY_POLICY.max_attempts, "baseDelay": QUERY_POLICY.base_delay, "maxDelay": QUERY_POLICY.max_delay, "leaseSeconds": QUERY_POLICY.lease_seconds, "approval": "technical_default_only"}}

    def require_scope(self, c, order):
        existing = c.execute("SELECT * FROM reconciliation_order_scopes WHERE order_id=?", (order["id"],)).fetchone()
        scope = self.scope()
        if existing and (scope is None or tuple(existing[k] for k in ("provider", "mode", "merchant_hash")) != scope.values):
            raise ApiError(409, "원래 주문에 연결된 상점·환경에서 처리해주세요.")
        return scope, existing

    def result_scope_matches(self, order, payment):
        with self.app.db.connect() as c:
            binding = c.execute("SELECT * FROM reconciliation_order_scopes WHERE order_id=?", (order["id"],)).fetchone()
            if not binding:
                return True  # Existing optional, unconfigured commerce contract.
            scope = self.scope()
            mid = payment.get("mId") if isinstance(payment, dict) else None
            return bool(scope and isinstance(mid, str) and hashed(mid) == scope.merchant_hash and tuple(binding[k] for k in ("provider", "mode", "merchant_hash")) == scope.values)

    def bind(self, c, actor, order, origin):
        scope, existing = self.require_scope(c, order)
        if not scope:
            return None  # Unconfigured optional recovery never fabricates a MID.
        if order["mode"] != scope.mode:
            raise ApiError(409, "주문과 현재 상점 환경이 다릅니다.")
        if not existing:
            stamp = at(self.clock)
            c.execute("INSERT INTO reconciliation_order_scopes VALUES(?,?,?,?,?,?,?,?)", (order["id"], *scope.values, hashed(order["id"]), order["version"], actor["id"], stamp))
            c.execute("INSERT INTO reconciliation_scope_observations VALUES(?,?,?,?,?,?,?,?,?)", (new_id("scopeobs"), order["id"], *scope.values, origin, actor["id"], order["version"], stamp))
        return scope

    def bind_checkout(self, c, actor, oid):
        order = c.execute("SELECT * FROM purchase_orders WHERE id=?", (oid,)).fetchone()
        return self.bind(c, actor, order, "checkout")

    def _schedule(self, c, actor, order, phase, reference, operation=None):
        scope = self.scope()
        existing = c.execute("SELECT job_id FROM reconciliation_schedules WHERE order_id=? AND phase=? AND reference_id=?", (order["id"], phase, reference)).fetchone()
        if existing:
            return existing[0]
        stamp, jid = at(self.clock), new_id("reconjob")
        c.execute("INSERT INTO reconciliation_schedules VALUES(?,?,?,?,?,?,?,?)", (jid, order["id"], phase, reference, operation, actor["id"], order["version"], stamp))
        c.execute("INSERT INTO reconciliation_jobs(id,order_id,provider,mode,merchant_hash,source,state,version,attempt_count,policy_version,max_attempts,base_delay,max_delay,lease_seconds,available_at,created_at,updated_at) VALUES(?,?,?,?,?,'scheduled','queued',1,0,1,?,?,?,?,?,?,?)",
                  (jid, order["id"], *scope.values, *QUERY_POLICY.values, stamp, stamp, stamp))
        return jid

    def schedule_authorization(self, c, actor, order, origin="approval", reference=None):
        scope = self.bind(c, actor, order, origin)
        if not scope:
            return None
        if reference is None:
            reference = audit(c, actor, "payment.approval_started", "purchase", order["id"], {"source": "explicit_confirmation", "amount": order["amount"]})
        return self._schedule(c, actor, order, "authorization", reference)

    def schedule_cancellation(self, c, actor, order, operation, command):
        scope, binding = self.require_scope(c, order)
        # A historical paid order is not assigned a merchant merely because a
        # recovery worker was enabled. Its operator first acknowledges scope.
        if not scope or not binding:
            return None
        current = c.execute("SELECT * FROM purchase_orders WHERE id=?", (order["id"],)).fetchone()
        return self._schedule(c, actor, current, "refund", command["id"], operation["id"])

    def _attach(self, c, scope, limit=50):
        # Only actually authenticated, non-conflicting order references qualify.
        rows = c.execute("SELECT e.* FROM provider_events e JOIN provider_event_auth a ON a.event_id=e.id JOIN reconciliation_order_scopes s ON s.provider=e.provider AND s.mode=e.mode AND s.merchant_hash=e.merchant_hash AND s.order_reference_hash=e.order_reference_hash WHERE e.provider=? AND e.mode=? AND e.merchant_hash=? AND a.disposition!='scope_mismatch' AND NOT EXISTS(SELECT 1 FROM provider_event_links l WHERE l.event_id=e.id) AND NOT EXISTS(SELECT 1 FROM provider_event_conflicts f WHERE f.event_id=e.id) ORDER BY e.rowid LIMIT ?", (*scope.values, limit)).fetchall()
        for event in rows:
            binding = c.execute("SELECT * FROM reconciliation_order_scopes WHERE provider=? AND mode=? AND merchant_hash=? AND order_reference_hash=?", (*scope.values, event["order_reference_hash"])).fetchone()
            self.model._link(c, event, binding, at(self.clock), QUERY_POLICY)
        return len(rows)

    def webhook(self, h):
        if h.command != "POST":
            raise ApiError(405, "웹훅은 POST 수신으로만 제공합니다.")
        if "?" in h.path:
            raise ApiError(400, "등록한 웹훅 경로로 전달해주세요.")
        gateway, scope = self.commerce.gateway, self.scope()
        keys = getattr(gateway, "webhook_security_keys", ())
        if not scope or not keys:
            raise ApiError(503, "웹훅 수신 환경을 준비 중입니다.")
        try:
            self.app.limiter.check(("webhook-ip", h.client_address[0]), 120, 60, busy_timeout_ms=500)
            raw = read_raw(h)
            proof = verify_signature(raw, h.headers, keys)
            event = parse_event(raw)
            self.app.limiter.check(("webhook-scope", *scope.values), 300, 60, busy_timeout_ms=500)
            with self.app.db.connect() as c:
                c.execute("PRAGMA busy_timeout=2000")
                c.execute("BEGIN IMMEDIATE")
                if self.commerce.gateway is not gateway or self.scope() != scope:
                    raise ApiError(503, "웹훅 연결 설정을 다시 확인하고 재전달해주세요.")
                receipt = self.model.record_event(scope, event["type"], event["payload"], event["reference"], event["identity"], QUERY_POLICY, connection=c, enqueue=False)
                if receipt["conflict"]:
                    return 200, {"accepted": True, "disposition": "conflict"}, {}
                disposition = "scope_mismatch" if event["merchantHash"] and event["merchantHash"] != scope.merchant_hash else "eligible" if event["reference"] else "unresolved_reference"
                current = c.execute("SELECT 1 FROM provider_event_auth WHERE event_id=?", (receipt["id"],)).fetchone()
                if not current:
                    c.execute("INSERT INTO provider_event_auth VALUES(?,'hmac_sha256_v1',?,?,?,?,?,?,?,?)", (receipt["id"], proof["rawBodyHash"], proof["transmissionTimeHash"], event["keyHash"], event["secretHash"], event["transactionHash"], event["hint"], disposition, at(self.clock)))
                self._attach(c, scope)
                linked = c.execute("SELECT 1 FROM provider_event_links WHERE event_id=?", (receipt["id"],)).fetchone()
                state = "quarantined" if disposition == "scope_mismatch" else "duplicate" if receipt["replayed"] else "queued" if linked else "unresolved"
                return 200, {"accepted": True, "disposition": state}, {}
        except sqlite3.Error:
            raise ApiError(503, "웹훅 수신 기록을 확정하지 못했습니다. 같은 사건을 재전달해주세요.") from None

    def _order(self, c, user, oid):
        actor = self.model._operator(c, user)
        order = c.execute("SELECT * FROM purchase_orders WHERE id=?", (oid,)).fetchone()
        if not order:
            raise ApiError(404, "주문을 찾을 수 없습니다.")
        return actor, order

    def _command_result(self, c, command, replayed=False):
        job = c.execute("SELECT * FROM reconciliation_jobs WHERE id=?", (command["job_id"],)).fetchone()
        return {"job": job_view(job), "receipt": {"orderId": command["order_id"], "jobId": command["job_id"],
                "priorJobId": command["prior_job_id"], "expectedOrderVersion": command["expected_order_version"],
                "expectedJobVersion": command["expected_job_version"], "reason": command["reason"],
                "scopeAcknowledged": bool(command["scope_acknowledged"]), "createdAt": command["created_at"]}, "replayed": replayed}

    def _find_command(self, c, actor, oid, key_hash, request_hash):
        row = c.execute("SELECT * FROM reconciliation_commands WHERE actor_id=? AND request_key_hash=?", (actor["id"], key_hash)).fetchone()
        if row and (row["order_id"] != oid or row["request_hash"] != request_hash):
            raise ApiError(409, "같은 대사 요청 번호의 내용이 다릅니다. 원래 요청을 먼저 확인해주세요.")
        return row

    def request_check(self, user, oid, data):
        base = {"requestKey", "expectedOrderVersion", "reason", "acknowledgeCurrentScope"}
        if not isinstance(data, dict) or set(data) not in (base, base | {"jobId", "expectedJobVersion"}):
            raise ApiError(400, "조회 사유·현재 판본·요청 번호와 상점 범위 확인을 지정해주세요.")
        key_hash = hashed(opaque(data["requestKey"], 120, 12))
        reason = text_field(data, "reason", minimum=10, maximum=1000)
        version = data["expectedOrderVersion"]
        if type(version) is not int or version < 1 or type(data["acknowledgeCurrentScope"]) is not bool:
            raise ApiError(400, "현재 주문 판본과 범위 확인 여부를 확인해주세요.")
        parent = data.get("jobId")
        if "jobId" in data and (not isinstance(parent, str) or not parent or type(data["expectedJobVersion"]) is not int or data["expectedJobVersion"] < 1):
            raise ApiError(400, "기존 작업과 현재 작업 판본을 확인해주세요.")
        request_hash = hashed(document({"orderId": oid, "body": data}))
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            actor, _ = self._order(c, user, oid)
            command = self._find_command(c, actor, oid, key_hash, request_hash)
            if command:
                return self._command_result(c, command, True)
        self.app.limiter.check(("query-request", actor["id"]), 30, 60)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            actor, order = self._order(c, user, oid)
            command = self._find_command(c, actor, oid, key_hash, request_hash)
            if command:
                return self._command_result(c, command, True)
            checked_version(order, version)
            if order["payment_key"] and order["payment_key"] in reason:
                raise ApiError(400, "조회 사유에서 결제 식별자 원문을 제외해주세요.")
            scope, binding = self.require_scope(c, order)
            if not scope:
                raise ApiError(503, "주문의 상점·환경과 조회 자격을 먼저 설정해주세요.")
            if not binding:
                if not data["acknowledgeCurrentScope"]:
                    raise ApiError(409, "과거 상점을 추정하지 않습니다. 현재 상점 범위의 명시 확인이 필요합니다.")
                self.bind(c, actor, order, "operator")
                binding = self.model._bound(c, scope, oid)
            if parent:
                prior = c.execute("SELECT * FROM reconciliation_jobs WHERE id=? AND order_id=?", (parent, oid)).fetchone()
                if not prior:
                    raise ApiError(404, "이 주문의 기존 작업을 찾을 수 없습니다.")
                if prior["version"] != data["expectedJobVersion"]:
                    raise ApiError(409, "기존 작업의 판본이 바뀌었습니다. 현재 상태를 다시 확인해주세요.")
            if c.execute("SELECT 1 FROM reconciliation_jobs WHERE order_id=? AND state IN ('queued','running','retry')", (oid,)).fetchone():
                raise ApiError(409, "이미 대기·실행·재시도 중인 같은 주문의 작업이 있습니다.")
            if c.execute("SELECT 1 FROM reconciliation_jobs WHERE requested_by=? AND request_key_hash=?", (actor["id"], key_hash)).fetchone():
                raise ApiError(409, "이미 사용한 내부 요청 번호입니다. 다른 요청으로 덮어쓸 수 없습니다.")
            jid = self.model._queue(c, binding, QUERY_POLICY, at(self.clock), actor_id=actor["id"], key_hash=key_hash, expected=version)
            c.execute("INSERT INTO reconciliation_commands VALUES(?,?,?,?,?,?,?,?,?,?,?)", (actor["id"], key_hash, request_hash, oid, parent, data.get("expectedJobVersion"), version, jid, reason, int(data["acknowledgeCurrentScope"]), at(self.clock)))
            audit(c, actor, "payment.query_requested", "purchase", oid, {"jobId": jid, "priorJobId": parent, "requestHash": request_hash})
            command = c.execute("SELECT * FROM reconciliation_commands WHERE actor_id=? AND request_key_hash=?", (actor["id"], key_hash)).fetchone()
            return self._command_result(c, command)

    def _page(self, query, allowed=()):
        if set(query) - {"before", "limit", *allowed} or any(len(v) != 1 for v in query.values()):
            raise ApiError(400, "조회 필터와 페이지를 확인해주세요.")
        def integer(key, default, maximum):
            value = query.get(key, [str(default)])[0]
            if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 15:
                raise ApiError(400, "조회 페이지의 정수를 확인해주세요.")
            number = int(value)
            if not 1 <= number <= maximum:
                raise ApiError(400, "조회 페이지 한도를 확인해주세요.")
            return number
        return integer("before", 2**47, 2**47), integer("limit", 30, 50)

    def read_order(self, user, oid, query):
        before, limit = self._page(query)
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            _, order = self._order(c, user, oid)
            binding = c.execute("SELECT * FROM reconciliation_order_scopes WHERE order_id=?", (oid,)).fetchone()
            scope = self.scope()
            rows = c.execute("SELECT rowid AS cursor,* FROM reconciliation_jobs WHERE order_id=? AND rowid<? ORDER BY rowid DESC LIMIT ?", (oid, before, limit + 1)).fetchall()
            jobs = []
            for row in rows[:limit]:
                attempts = c.execute("SELECT a.id,a.ordinal,a.started_at,a.lease_until,a.state,a.result_code,a.observed_status,a.observed_amount,a.observed_currency,a.finished_at,o.detail_code FROM reconciliation_attempts a LEFT JOIN reconciliation_outcomes o ON o.attempt_id=a.id WHERE a.job_id=? ORDER BY a.ordinal DESC LIMIT 10", (row["id"],)).fetchall()
                command = c.execute("SELECT reason,created_at,actor_id FROM reconciliation_commands WHERE job_id=?", (row["id"],)).fetchone()
                schedule = c.execute("SELECT phase,reference_id,initiated_by,created_at FROM reconciliation_schedules WHERE job_id=?", (row["id"],)).fetchone()
                jobs.append({**job_view(row), "attempts": [dict(a) for a in attempts], "request": dict(command) if command else None, "schedule": dict(schedule) if schedule else None})
            events = c.execute("SELECT e.id,e.event_type,e.identity_kind,e.received_at,l.linked_at,a.algorithm,a.hint_status,a.disposition FROM provider_event_links l JOIN provider_events e ON e.id=l.event_id LEFT JOIN provider_event_auth a ON a.event_id=e.id WHERE l.order_id=? ORDER BY e.rowid DESC LIMIT 30", (oid,)).fetchall()
            return {"schemaVersion": 1, "order": {"id": oid, "version": order["version"], "status": order["status"], "mode": order["mode"]},
                    "configuration": self.describe(), "scope": {"recorded": binding is not None, "observedAt": binding["observed_at"] if binding else None,
                        "matchesCurrent": bool(scope and binding and tuple(binding[k] for k in ("provider", "mode", "merchant_hash")) == scope.values), "coverage": "recorded_observations_only"},
                    "jobs": jobs, "events": [dict(e) for e in events], "page": {"limit": limit, "nextBefore": rows[limit-1]["cursor"] if len(rows) > limit else None}}

    def queue_read(self, user, query, events=False):
        before, limit = self._page(query, ("state",) if not events else ())
        state = query.get("state", ["review"])[0]
        if not events and state not in ("queued", "running", "retry", "review", "matched"):
            raise ApiError(400, "작업 상태 필터를 확인해주세요.")
        with self.app.db.connect() as c:
            c.execute("BEGIN")
            self.model._operator(c, user)
            if events:
                rows = c.execute("SELECT e.rowid AS cursor,e.id,e.mode,e.event_type,e.received_at,a.algorithm,a.disposition FROM provider_events e LEFT JOIN provider_event_auth a ON a.event_id=e.id WHERE e.rowid<? AND (NOT EXISTS(SELECT 1 FROM provider_event_links l WHERE l.event_id=e.id) OR EXISTS(SELECT 1 FROM provider_event_conflicts f WHERE f.event_id=e.id)) ORDER BY e.rowid DESC LIMIT ?", (before, limit+1)).fetchall()
                items = [{k:r[k] for k in r.keys() if k != "cursor"} for r in rows[:limit]]
            else:
                rows = c.execute("SELECT rowid AS cursor,* FROM reconciliation_jobs WHERE state=? AND rowid<? ORDER BY rowid DESC LIMIT ?", (state, before, limit+1)).fetchall()
                items = [job_view(r) for r in rows[:limit]]
            return {"configuration": self.describe(), "items": items, "page": {"limit": limit, "nextBefore": rows[limit-1]["cursor"] if len(rows)>limit else None}}

    def route(self, h, path, method, query):
        if path in ("/api/manage/reconciliation", "/api/manage/reconciliation/events"):
            if method != "GET":
                raise ApiError(405, "작업 큐는 조회로만 제공합니다.")
            return 200, self.queue_read(h.user, query, path.endswith("/events")), {}
        match = re.fullmatch(r"/api/manage/purchases/([a-z0-9_]+)/reconciliation", path)
        if not match:
            return None
        if method == "GET":
            return 200, self.read_order(h.user, match.group(1), query), {}
        if method == "POST":
            if query:
                raise ApiError(400, "명시 조회 요청에 검색 조건을 붙일 수 없습니다.")
            value = self.request_check(h.user, match.group(1), h.body())
            return 200 if value["replayed"] else 201, value, {}
        raise ApiError(405, "지원하지 않는 대사 요청입니다.")

    def run_one(self, worker="server-query-worker"):
        gateway, scope = self.commerce.gateway, self.scope()
        if not scope:
            return None
        self.app.limiter.check(("provider-query", *scope.values), 60, 60)
        with self.app.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self._attach(c, scope)
        claim = self.work.claim(scope, worker)
        if claim is None:
            return None
        with self.app.db.connect() as c:
            job = c.execute("SELECT * FROM reconciliation_jobs WHERE id=?", (claim.job_id,)).fetchone()
            order = c.execute("SELECT * FROM purchase_orders WHERE id=?", (job["order_id"],)).fetchone()
        if order["payment_key"]:
            lookup = getattr(gateway, "lookup_payment", None)
            argument = order["payment_key"]
        else:
            supported = getattr(gateway, "can_lookup_order", None)
            lookup = getattr(gateway, "lookup_order", None) if callable(supported) and supported() else None
            argument = order["id"]
        if not callable(lookup):
            return self.work.fail(scope, claim, "lookup_error", "lookup_unavailable", True)
        try:
            payment = lookup(argument)  # No writer lock and no confirm/cancel POST.
        except (GatewayError, TimeoutError, OSError) as error:
            timeout = isinstance(error, TimeoutError) or isinstance(error.__cause__, TimeoutError) or getattr(error, "kind", None) == "timeout"
            return self.work.fail(scope, claim, "lookup_timeout" if timeout else "lookup_error", "lookup_timeout" if timeout else "lookup_failed")
        try:
            return self.work.settle(scope, claim, payment, self.processor, lambda: self.commerce.gateway is gateway and self.scope() == scope)
        except sqlite3.Error:
            # The SQL effect and completion have rolled back together. Retry the
            # same durable work by a fresh GET, never by another financial POST.
            return self.work.fail(scope, claim, "lookup_error", "apply_failed")

    def run_batch(self, limit=20, worker="query-cli"):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Query batch must contain 1–20 jobs")
        counts = {"matched": 0, "retry": 0, "review": 0, "deferred": 0}
        for _ in range(limit):
            if self.stop_event.is_set():
                break
            try:
                row = self.run_one(worker)
            except ApiError as error:
                if error.status not in (409, 429):
                    raise
                counts["deferred"] += 1
                break
            if row is None:
                break
            counts[row["state"]] += 1
        return counts

    def start(self, enabled=False):
        if not enabled or not self.scope() or (self.thread and self.thread.is_alive()):
            return False
        self.stop_event.clear()
        def loop():
            while not self.stop_event.is_set():
                try:
                    self.run_batch(10, "managed-server-worker")
                except Exception:
                    logging.getLogger("tpc").warning("Payment query worker deferred a batch; consult the safe reconciliation queue.")
                self.stop_event.wait(2)
        self.thread = threading.Thread(target=loop, name="tpc-payment-query", daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=22)
