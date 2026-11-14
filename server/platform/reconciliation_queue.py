"""Durable leased query work. No PG I/O or automatic worker is started here."""

import json
import secrets
import time

from ..core import ApiError, new_id
from .reconciliation_contract import Claim, Scope, document, hashed, opaque, order_facts, query_match


def at(clock):
    value = clock()
    if type(value) not in (int, float) or not 0 <= value < 2 ** 53:
        raise ApiError(500, "대사 시계를 확인할 수 없습니다.")
    return int(value)


def scoped(scope):
    if not isinstance(scope, Scope):
        raise ApiError(400, "서버의 대사 범위를 지정해주세요.")
    return scope.values


def job_view(row):
    return {"id": row["id"], "orderId": row["order_id"], "source": row["source"], "eventId": row["event_id"],
            "state": row["state"], "version": row["version"], "attemptCount": row["attempt_count"],
            "availableAt": row["available_at"], "leaseUntil": row["lease_until"], "createdAt": row["created_at"],
            "updatedAt": row["updated_at"], "policy": {"version": row["policy_version"], "maxAttempts": row["max_attempts"],
                "baseDelay": row["base_delay"], "maxDelay": row["max_delay"], "leaseSeconds": row["lease_seconds"], "approval": "technical_default_only"}}


class ReconciliationQueue:
    def __init__(self, db, clock=time.time):
        self.db, self.clock = db, clock

    def _finish(self, c, job, attempt, state, code, stamp, payment=None):
        payment = payment if isinstance(payment, dict) else {}
        status = payment.get("status")
        from .reconciliation_contract import PG_STATUSES
        status = status if isinstance(status, str) and status in PG_STATUSES else None
        amount = payment.get("totalAmount")
        amount = amount if type(amount) is int and 0 <= amount < 2 ** 53 else None
        key = payment.get("paymentKey")
        key = hashed(key) if isinstance(key, str) and 1 <= len(key) <= 1000 else None
        final = "review" if state == "retry" and job["attempt_count"] >= job["max_attempts"] else state
        delay = min(job["max_delay"], job["base_delay"] * (2 ** (job["attempt_count"] - 1)))
        c.execute("UPDATE reconciliation_attempts SET state=?,result_code=?,observed_status=?,observed_amount=?,observed_currency=?,observed_key_hash=?,finished_at=? WHERE id=?",
                  ("expired" if code == "lease_expired" else final, code, status, amount, "KRW" if payment.get("currency") == "KRW" else None, key, stamp, attempt["id"]))
        c.execute("UPDATE reconciliation_jobs SET state=?,version=version+1,available_at=?,lease_until=NULL,updated_at=? WHERE id=?",
                  (final, stamp + delay if final == "retry" else stamp, stamp, job["id"]))
        return job_view(c.execute("SELECT * FROM reconciliation_jobs WHERE id=?", (job["id"],)).fetchone())

    def claim(self, scope, worker):
        """Only one unexpired runner per order; expired owners are fenced forever."""
        values = scoped(scope)
        worker_hash = hashed(opaque(worker, 200))
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            stamp = at(self.clock)
            expired = c.execute("SELECT * FROM reconciliation_jobs WHERE provider=? AND mode=? AND merchant_hash=? AND state='running' AND lease_until<=?", (*values, stamp)).fetchall()
            for job in expired:
                attempt = c.execute("SELECT * FROM reconciliation_attempts WHERE id=?", (job["current_attempt_id"],)).fetchone()
                self._finish(c, job, attempt, "retry", "lease_expired", stamp)
            job = c.execute("SELECT * FROM reconciliation_jobs j WHERE provider=? AND mode=? AND merchant_hash=? AND state IN ('queued','retry') AND available_at<=? AND attempt_count<max_attempts AND NOT EXISTS(SELECT 1 FROM reconciliation_jobs busy WHERE busy.order_id=j.order_id AND busy.state='running') ORDER BY available_at,created_at,rowid LIMIT 1", (*values, stamp)).fetchone()
            if not job:
                return None
            order = c.execute("SELECT * FROM purchase_orders WHERE id=?", (job["order_id"],)).fetchone()
            token, aid = secrets.token_urlsafe(32), new_id("reconattempt")
            until = stamp + job["lease_seconds"]
            c.execute("INSERT INTO reconciliation_attempts(id,job_id,ordinal,worker_hash,token_hash,order_facts,started_at,lease_until,state) VALUES(?,?,?,?,?,?,?,?,'running')",
                      (aid, job["id"], job["attempt_count"] + 1, worker_hash, hashed(token), document(order_facts(order)), stamp, until))
            c.execute("UPDATE reconciliation_jobs SET state='running',version=version+1,attempt_count=attempt_count+1,lease_until=?,current_attempt_id=?,updated_at=? WHERE id=?", (until, aid, stamp, job["id"]))
            return Claim(job["id"], aid, token)

    def _owned(self, c, scope, claim, stamp):
        values = scoped(scope)
        if not isinstance(claim, Claim) or not all(isinstance(v, str) for v in (claim.job_id, claim.attempt_id, claim.token)):
            raise ApiError(400, "대사 실행 소유권을 확인해주세요.")
        job = c.execute("SELECT * FROM reconciliation_jobs WHERE id=? AND provider=? AND mode=? AND merchant_hash=?", (claim.job_id, *values)).fetchone()
        attempt = c.execute("SELECT * FROM reconciliation_attempts WHERE id=? AND job_id=? AND token_hash=?", (claim.attempt_id, claim.job_id, hashed(claim.token))).fetchone()
        if not job or not attempt or job["state"] != "running" or attempt["state"] != "running" or job["current_attempt_id"] != claim.attempt_id or job["lease_until"] <= stamp:
            raise ApiError(409, "만료되거나 인계된 대사 실행입니다. 현재 작업을 다시 확인해주세요.")
        return job, attempt

    def failed_lookup(self, scope, claim, code):
        if code not in ("lookup_timeout", "lookup_error"):
            raise ApiError(400, "조회 실패 유형을 확인해주세요.")
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            stamp = at(self.clock)
            job, attempt = self._owned(c, scope, claim, stamp)
            return self._finish(c, job, attempt, "retry", code, stamp)

    def complete_query(self, scope, claim, payment, apply):
        """Atomic model boundary for a future trusted PG lookup worker (.02).

        `payment` must be a fresh, same-scope adapter GET result, not event data.
        `apply(c, order, payment)` performs SQL-only business checks/effects on
        this same transaction and returns exactly True only on convergence.
        It must validate financial transition/balance/grant rules itself. This
        model validates identity and amount; it never confirms/cancels money.
        A raised exception, expired lease or rejected effect rolls back *all*
        effects and the completion. There is no public caller of this boundary.
        """
        if not callable(apply):
            raise ApiError(400, "원자적 거래 반영기를 지정해주세요.")
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            stamp = at(self.clock)
            job, attempt = self._owned(c, scope, claim, stamp)
            order = c.execute("SELECT * FROM purchase_orders WHERE id=?", (job["order_id"],)).fetchone()
            code = "order_changed" if document(order_facts(order)) != attempt["order_facts"] else query_match(scope, order, payment)
            if code != "pg_match":
                return self._finish(c, job, attempt, "review", code, stamp, payment)
            if apply(c, order, payment) is not True:
                raise ApiError(409, "거래·권한 상태가 일치하지 않아 반영을 보류했습니다.")
            finished = at(self.clock)
            if finished >= job["lease_until"]:
                raise ApiError(409, "대사 실행 기한이 지나 반영을 보류했습니다.")
            return self._finish(c, job, attempt, "matched", "pg_match", finished, payment)
