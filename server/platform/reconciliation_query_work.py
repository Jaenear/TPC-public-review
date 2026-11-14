"""v26 SQL outcomes on the v25 durable lease/attempt engine."""

from ..core import ApiError
from .reconciliation_contract import document, hashed, order_facts, query_match
from .reconciliation_processor import Decision, review, retry
from .reconciliation_queue import ReconciliationQueue, at


class QueryWork(ReconciliationQueue):
    def _record(self, c, claim, detail, stamp):
        c.execute("INSERT INTO reconciliation_outcomes VALUES(?,?,?,?)", (claim.attempt_id, claim.job_id, detail, stamp))

    def fail(self, scope, claim, code, detail, terminal=False):
        if code not in ("lookup_error", "lookup_timeout", "scope_mismatch"):
            raise ValueError("Invalid query failure code")
        with self.db.connect() as c:
            c.execute("PRAGMA busy_timeout=2000")
            c.execute("BEGIN IMMEDIATE")
            stamp = at(self.clock)
            job, attempt = self._owned(c, scope, claim, stamp)
            self._record(c, claim, detail, stamp)
            return self._finish(c, job, attempt, "review" if terminal else "retry", code, stamp)

    def _notice(self, c, job, payment):
        if job["source"] != "event":
            return None
        auth = c.execute("SELECT * FROM provider_event_auth WHERE event_id=?", (job["event_id"],)).fetchone()
        if not auth or auth["disposition"] == "scope_mismatch":
            return review("notice_auth_missing", "reference_mismatch")
        if auth["payment_key_hash"] and auth["payment_key_hash"] != hashed(payment["paymentKey"]):
            return review("notice_key_mismatch", "payment_key_mismatch")
        if auth["secret_hash"]:
            secret = payment.get("secret")
            if not isinstance(secret, str) or hashed(secret) != auth["secret_hash"]:
                return review("notice_secret_mismatch", "reference_mismatch")
        return None

    def settle(self, scope, claim, payment, processor, configuration_current):
        with self.db.connect() as c:
            c.execute("PRAGMA busy_timeout=2000")
            c.execute("BEGIN IMMEDIATE")
            stamp = at(self.clock)
            job, attempt = self._owned(c, scope, claim, stamp)
            order = c.execute("SELECT * FROM purchase_orders WHERE id=?", (job["order_id"],)).fetchone()
            if not configuration_current():
                choice = review("configuration_changed", "scope_mismatch")
            elif document(order_facts(order)) != attempt["order_facts"]:
                choice = retry("order_changed", "order_changed")
            else:
                code = query_match(scope, order, payment)
                choice = review("identity_mismatch", code) if code != "pg_match" else self._notice(c, job, payment)
            if choice is None:
                c.execute("SAVEPOINT query_business_effect")
                choice = processor(c, order, payment)
                if not isinstance(choice, Decision) or choice.state not in ("matched", "retry", "review"):
                    raise ValueError("Invalid query business decision")
                if choice.state != "matched":
                    c.execute("ROLLBACK TO query_business_effect")
                c.execute("RELEASE query_business_effect")
            finished = at(self.clock)
            if choice.state == "matched" and not configuration_current():
                raise ApiError(409, "조회 중 결제 연결 설정이 바뀌어 반영을 보류했습니다.")
            if finished >= job["lease_until"]:
                raise ApiError(409, "대사 실행 기한이 지나 반영을 보류했습니다.")
            self._record(c, claim, choice.detail, finished)
            return self._finish(c, job, attempt, choice.state, choice.code, finished, payment)
