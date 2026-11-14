"""Internal receipt/model service attached to Platform; no HTTP ingress yet.

Receipt records cannot authorize commerce changes. .02 supplies authenticated,
bounded ingress and configured PG lookup workers; .03 supplies operator routes.
"""

import time

from ..core import ApiError, new_id
from .common import require_role
from .order_products import active_actor
from .reconciliation_contract import EVENT_TYPES, TechnicalPolicy, checked_version, document, hashed, opaque
from .reconciliation_queue import ReconciliationQueue, at, job_view, scoped


class ReconciliationService:
    def __init__(self, app, clock=time.time):
        self.db, self.clock = app.db, clock
        self.queue = ReconciliationQueue(self.db, clock)

    def _operator(self, c, user):
        actor = active_actor(c, user)
        require_role(c, actor, "admin")
        return actor

    def _bound(self, c, scope, oid):
        binding = c.execute("SELECT * FROM reconciliation_order_scopes WHERE order_id=? AND provider=? AND mode=? AND merchant_hash=?", (opaque(oid), *scoped(scope))).fetchone()
        if not binding:
            raise ApiError(404, "이 상점·환경에 명시적으로 연결된 주문을 찾을 수 없습니다.")
        return binding

    def bind_order(self, user, scope, oid, expected_version):
        """Explicit current observation; never historical merchant certification."""
        values = scoped(scope)
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            actor = self._operator(c, user)
            order = c.execute("SELECT * FROM purchase_orders WHERE id=?", (opaque(oid),)).fetchone()
            if not order:
                raise ApiError(404, "주문을 찾을 수 없습니다.")
            checked_version(order, expected_version)
            if order["mode"] != scope.mode:
                raise ApiError(409, "주문과 상점 환경이 다릅니다.")
            existing = c.execute("SELECT * FROM reconciliation_order_scopes WHERE order_id=?", (oid,)).fetchone()
            if existing:
                if tuple(existing[k] for k in ("provider", "mode", "merchant_hash")) != values:
                    raise ApiError(409, "이미 다른 상점 범위에 연결된 주문입니다.")
                return {"orderId": oid, "observedAt": existing["observed_at"], "replayed": True}
            stamp = at(self.clock)
            c.execute("INSERT INTO reconciliation_order_scopes VALUES(?,?,?,?,?,?,?,?)", (oid, *values, hashed(oid), order["version"], actor["id"], stamp))
            return {"orderId": oid, "observedAt": stamp, "replayed": False}

    def _queue(self, c, binding, policy, stamp, event_id=None, actor_id=None, key_hash=None, expected=None):
        if not isinstance(policy, TechnicalPolicy):
            raise ApiError(400, "대사 기술 정책을 확인해주세요.")
        jid = new_id("reconjob")
        c.execute("INSERT INTO reconciliation_jobs(id,order_id,provider,mode,merchant_hash,source,event_id,requested_by,request_key_hash,request_order_version,state,version,attempt_count,policy_version,max_attempts,base_delay,max_delay,lease_seconds,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'queued',1,0,1,?,?,?,?,?,?,?)",
                  (jid, binding["order_id"], binding["provider"], binding["mode"], binding["merchant_hash"], "event" if event_id else "operator", event_id, actor_id, key_hash, expected, *policy.values, stamp, stamp, stamp))
        return jid

    def _link(self, c, event, binding, stamp, policy, actor_id=None):
        if event["order_reference_hash"] != binding["order_reference_hash"] or tuple(event[k] for k in ("provider", "mode", "merchant_hash")) != tuple(binding[k] for k in ("provider", "mode", "merchant_hash")):
            raise ApiError(409, "이벤트 참조와 주문 범위가 일치하지 않습니다.")
        linked = c.execute("SELECT order_id FROM provider_event_links WHERE event_id=?", (event["id"],)).fetchone()
        if linked:
            if linked["order_id"] != binding["order_id"]:
                raise ApiError(409, "다른 주문에 이미 연결된 이벤트입니다.")
            return c.execute("SELECT id FROM reconciliation_jobs WHERE event_id=?", (event["id"],)).fetchone()[0]
        c.execute("INSERT INTO provider_event_links VALUES(?,?,?,?,?,?,?,?)", (event["id"], binding["order_id"], binding["provider"], binding["mode"], binding["merchant_hash"], binding["order_reference_hash"], actor_id, stamp))
        return self._queue(c, binding, policy, stamp, event_id=event["id"])

    def record_event(self, scope, event_type, payload, order_reference=None, provider_event_id=None, policy=None, *, connection=None, enqueue=True):
        """Record normalized, untrusted ingress facts; discard the raw JSON.

        A future parser passes the official event's reference/optional opaque ID.
        The hash includes those normalized facts as well as the whole body. An
        id-less event uses canonical content identity, never a fabricated ID.
        """
        values = scoped(scope)
        if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
            raise ApiError(400, "지원하는 결제 이벤트 유형을 확인해주세요.")
        reference = opaque(order_reference) if order_reference is not None else None
        identity = opaque(provider_event_id, 64) if provider_event_id is not None else None
        document(payload)
        content_hash = hashed(document({"schemaVersion": 1, "eventType": event_type, "body": payload, "orderReference": reference, "providerEventId": identity}))
        kind, identity_hash = ("provider_id", hashed(identity)) if identity is not None else ("content", content_hash)
        policy = TechnicalPolicy() if policy is None else policy
        if not isinstance(policy, TechnicalPolicy):
            raise ApiError(400, "대사 기술 정책을 확인해주세요.")
        if type(enqueue) is not bool:
            raise ApiError(400, "이벤트 작업 연결을 확인해주세요.")
        prepared = (values, kind, identity_hash, content_hash, event_type, hashed(reference) if reference is not None else None)
        if connection is not None:
            if not connection.in_transaction:
                raise ValueError("Receipt caller must own a transaction")
            return self._store_event(connection, prepared, policy, enqueue)
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            return self._store_event(c, prepared, policy, enqueue)

    def _store_event(self, c, prepared, policy, enqueue):
        values, kind, identity_hash, content_hash, event_type, ref_hash = prepared
        existing = c.execute("SELECT * FROM provider_events WHERE provider=? AND mode=? AND merchant_hash=? AND identity_kind=? AND identity_hash=?", (*values, kind, identity_hash)).fetchone()
        stamp = at(self.clock)
        if existing:
            conflict = existing["content_hash"] != content_hash
            if conflict:
                c.execute("INSERT INTO provider_event_conflicts VALUES(?,?,?,?) ON CONFLICT(event_id,content_hash) DO NOTHING", (new_id("pgconflict"), existing["id"], content_hash, stamp))
            job = c.execute("SELECT id FROM reconciliation_jobs WHERE event_id=?", (existing["id"],)).fetchone()
            return {"id": existing["id"], "replayed": not conflict, "conflict": conflict, "jobId": job[0] if job else None}
        eid = new_id("pgevent")
        c.execute("INSERT INTO provider_events VALUES(?,?,?,?,?,?,?,?,?,?)", (eid, *values, kind, identity_hash, content_hash, event_type, ref_hash, stamp))
        binding = c.execute("SELECT * FROM reconciliation_order_scopes WHERE provider=? AND mode=? AND merchant_hash=? AND order_reference_hash=?", (*values, ref_hash)).fetchone() if ref_hash is not None and enqueue else None
        event = c.execute("SELECT * FROM provider_events WHERE id=?", (eid,)).fetchone()
        jid = self._link(c, event, binding, stamp, policy) if binding else None
        return {"id": eid, "replayed": False, "conflict": False, "jobId": jid}

    def link_event(self, user, scope, eid, oid, expected_version, policy=None):
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            actor = self._operator(c, user)
            binding = self._bound(c, scope, oid)
            order = c.execute("SELECT * FROM purchase_orders WHERE id=?", (oid,)).fetchone()
            checked_version(order, expected_version)
            event = c.execute("SELECT * FROM provider_events WHERE id=?", (opaque(eid),)).fetchone()
            if not event:
                raise ApiError(404, "이벤트를 찾을 수 없습니다.")
            if c.execute("SELECT 1 FROM provider_event_conflicts WHERE event_id=?", (eid,)).fetchone():
                raise ApiError(409, "내용 충돌이 기록된 이벤트는 별도로 조사해주세요.")
            return {"jobId": self._link(c, event, binding, at(self.clock), TechnicalPolicy() if policy is None else policy, actor["id"])}

    def request_check(self, user, scope, oid, expected_version, request_key, policy=None):
        key_hash = hashed(opaque(request_key, 120, 12))
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            actor = self._operator(c, user)
            binding = self._bound(c, scope, oid)
            if type(expected_version) is not int or expected_version < 1:
                raise ApiError(400, "주문 판본을 확인해주세요.")
            existing = c.execute("SELECT * FROM reconciliation_jobs WHERE requested_by=? AND request_key_hash=?", (actor["id"], key_hash)).fetchone()
            if existing:
                if existing["order_id"] != oid or existing["request_order_version"] != expected_version:
                    raise ApiError(409, "같은 대사 요청 번호의 내용이 다릅니다.")
                return {"job": job_view(existing), "replayed": True}
            order = c.execute("SELECT * FROM purchase_orders WHERE id=?", (oid,)).fetchone()
            checked_version(order, expected_version)
            jid = self._queue(c, binding, TechnicalPolicy() if policy is None else policy, at(self.clock), actor_id=actor["id"], key_hash=key_hash, expected=expected_version)
            return {"job": job_view(c.execute("SELECT * FROM reconciliation_jobs WHERE id=?", (jid,)).fetchone()), "replayed": False}

    def history(self, user, scope, oid):
        """Bounded, read-only internal operator projection; no raw bodies/tokens."""
        with self.db.connect() as c:
            c.execute("BEGIN")
            self._operator(c, user)
            binding = self._bound(c, scope, oid)
            events = c.execute("SELECT e.id,e.event_type,e.identity_kind,e.content_hash,e.received_at,l.linked_at FROM provider_events e JOIN provider_event_links l ON l.event_id=e.id WHERE l.order_id=? ORDER BY e.rowid DESC LIMIT 50", (oid,)).fetchall()
            jobs = c.execute("SELECT * FROM reconciliation_jobs WHERE order_id=? ORDER BY rowid DESC LIMIT 50", (oid,)).fetchall()
            attempts = c.execute("SELECT a.id,a.job_id,a.ordinal,a.started_at,a.lease_until,a.state,a.result_code,a.observed_status,a.observed_amount,a.observed_currency,a.finished_at FROM reconciliation_attempts a JOIN reconciliation_jobs j ON j.id=a.job_id WHERE j.order_id=? ORDER BY a.rowid DESC LIMIT 100", (oid,)).fetchall()
            return {"schemaVersion": 1, "orderId": oid, "mode": scope.mode, "scopeObservedAt": binding["observed_at"],
                    "coverage": "recorded_observations_only", "limits": {"events": 50, "jobs": 50, "attempts": 100},
                    "events": [dict(r) for r in events], "jobs": [job_view(r) for r in jobs], "attempts": [dict(r) for r in attempts]}
