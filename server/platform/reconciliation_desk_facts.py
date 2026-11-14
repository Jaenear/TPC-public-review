"""A bounded, secret-free operator projection of current financial facts.

The SQL context contains hashes and record versions, never payment key bytes.
It deliberately excludes review records, wall-clock ticks and display labels:
writing a note must not invalidate itself, but a real new result/grant must.
"""

import json

from .reconciliation_contract import document, hashed


ORDER_CTE = r"""
WITH job_totals AS (
 SELECT order_id,count(*) AS job_count,sum(version) AS job_versions,sum(attempt_count) AS attempt_count,
 sum(state IN ('queued','running','retry')) AS active_count,
 min(CASE WHEN state IN ('queued','retry') THEN available_at END) AS next_at,
 max(CASE WHEN state='running' THEN lease_until END) AS lease_until,max(rowid) AS last_row
 FROM reconciliation_jobs GROUP BY order_id
), last_observation AS (
 SELECT a.*,j.order_id,o.detail_code,row_number() OVER(PARTITION BY j.order_id ORDER BY a.finished_at DESC,a.rowid DESC) AS position
 FROM reconciliation_attempts a JOIN reconciliation_jobs j ON j.id=a.job_id
 LEFT JOIN reconciliation_outcomes o ON o.attempt_id=a.id WHERE a.finished_at IS NOT NULL
), own_grants AS (
 SELECT g.source_id AS order_id,count(*) AS raw_count,
 sum(e.user_id=o.user_id AND e.course_id=o.course_id) AS valid_count,
 sum(e.user_id!=o.user_id OR e.course_id!=o.course_id) AS bad_count,
 sum(e.user_id=o.user_id AND e.course_id=o.course_id AND g.status='active' AND (g.starts_at IS NULL OR g.starts_at<=:as_of) AND (g.expires_at IS NULL OR g.expires_at>:as_of)) AS active_count,
 sum(e.user_id=o.user_id AND e.course_id=o.course_id AND g.status='active' AND g.starts_at>:as_of AND (g.expires_at IS NULL OR g.expires_at>:as_of)) AS scheduled_count,
 sum(e.user_id=o.user_id AND e.course_id=o.course_id AND g.status='active' AND g.expires_at IS NOT NULL AND g.expires_at<=:as_of) AS expired_count,
 sum(e.user_id=o.user_id AND e.course_id=o.course_id AND g.status='revoked') AS revoked_count,
 sum(g.version) AS versions,max(g.rowid) AS last_row
 FROM enrollment_grants g JOIN enrollments e ON e.id=g.enrollment_id JOIN purchase_orders o ON o.id=g.source_id
 WHERE g.source='payment' GROUP BY g.source_id
), all_grants AS (
 SELECT e.user_id,e.course_id,count(*) AS total,sum(g.version) AS versions,max(g.rowid) AS last_row,
 sum(g.status='active' AND (g.starts_at IS NULL OR g.starts_at<=:as_of) AND (g.expires_at IS NULL OR g.expires_at>:as_of)) AS active_count,
 sum(g.status='active' AND g.starts_at>:as_of AND (g.expires_at IS NULL OR g.expires_at>:as_of)) AS scheduled_count,
 sum(g.status='active' AND g.expires_at IS NOT NULL AND g.expires_at<=:as_of) AS expired_count,
 sum(g.status='revoked') AS revoked_count
 FROM enrollment_grants g JOIN enrollments e ON e.id=g.enrollment_id GROUP BY e.user_id,e.course_id
), payment_facts AS (
 SELECT order_id,count(*) AS count,max(rowid) AS last_row FROM payment_events GROUP BY order_id
), receipt_totals AS (
 SELECT l.order_id,count(DISTINCT l.event_id) AS count,count(DISTINCT a.event_id) AS authenticated,
 count(f.id) AS conflicts,max(f.rowid) AS last_conflict,max(l.rowid) AS last_link
 FROM provider_event_links l LEFT JOIN provider_event_auth a ON a.event_id=l.event_id
 LEFT JOIN provider_event_conflicts f ON f.event_id=l.event_id GROUP BY l.order_id
), base AS (
 SELECT o.rowid AS cursor,o.id,o.course_id,o.title,o.amount,o.currency,o.mode,o.status,o.version,o.created_at,o.updated_at,o.paid_at,
 CASE WHEN o.provider_status IN ('READY','IN_PROGRESS','WAITING_FOR_DEPOSIT','DONE','CANCELED','PARTIAL_CANCELED','ABORTED','EXPIRED') THEN o.provider_status END AS provider_status,
 tpc_hash(o.payment_key) AS current_key_hash,
 COALESCE(jt.job_count,0) AS job_count,COALESCE(jt.job_versions,0) AS job_versions,COALESCE(jt.attempt_count,0) AS attempt_count,
 COALESCE(jt.active_count,0) AS active_count,jt.next_at,jt.lease_until,
 j.id AS last_job_id,j.state AS last_job_state,j.version AS last_job_version,j.attempt_count AS last_job_attempts,j.max_attempts,
 a.id AS observation_id,a.job_id AS observation_job,a.ordinal AS observation_ordinal,a.state AS observation_state,
 a.result_code,a.detail_code,CASE WHEN a.observed_status IN ('READY','IN_PROGRESS','WAITING_FOR_DEPOSIT','DONE','CANCELED','PARTIAL_CANCELED','ABORTED','EXPIRED') THEN a.observed_status END AS observed_status,
 a.observed_amount,a.observed_currency,a.observed_key_hash,a.order_facts,a.finished_at,
 COALESCE(pg.raw_count,0) AS payment_count,COALESCE(pg.valid_count,0) AS valid_payment_count,COALESCE(pg.bad_count,0) AS bad_grant_count,
 COALESCE(pg.active_count,0) AS payment_active,COALESCE(pg.scheduled_count,0) AS payment_scheduled,
 COALESCE(pg.expired_count,0) AS payment_expired,COALESCE(pg.revoked_count,0) AS payment_revoked,
 COALESCE(pg.versions,0) AS payment_grant_versions,COALESCE(pg.last_row,0) AS payment_grant_last,
 COALESCE(ag.total,0) AS grant_count,COALESCE(ag.versions,0) AS grant_versions,COALESCE(ag.last_row,0) AS grant_last,
 COALESCE(ag.active_count,0) AS grant_active,COALESCE(ag.scheduled_count,0) AS grant_scheduled,
 COALESCE(ag.expired_count,0) AS grant_expired,COALESCE(ag.revoked_count,0) AS grant_revoked,
 COALESCE(pf.count,0) AS payment_event_count,COALESCE(pf.last_row,0) AS payment_event_last,
 COALESCE(rt.count,0) AS receipt_count,COALESCE(rt.authenticated,0) AS authenticated_count,
 COALESCE(rt.conflicts,0) AS receipt_conflicts,COALESCE(rt.last_conflict,0) AS receipt_last_conflict,COALESCE(rt.last_link,0) AS receipt_last_link,
 ro.id AS refund_operation,ro.version AS refund_version,ro.status AS refund_state,
 s.order_id IS NOT NULL AS scope_recorded,s.mode AS scope_mode,s.merchant_hash AS scope_merchant,
 (s.order_id IS NOT NULL AND s.mode=:mode AND s.merchant_hash=:merchant) AS scope_matches,
 c.id AS case_id,c.version AS case_version,c.state AS case_state,c.owner_id,c.context_json AS case_context,
 c.updated_at AS case_updated,u.name AS owner_name,u.status AS owner_status,u.role AS owner_role,
 CAST(strftime('%s',o.updated_at) AS INTEGER) AS changed_at
 FROM purchase_orders o
 LEFT JOIN job_totals jt ON jt.order_id=o.id LEFT JOIN reconciliation_jobs j ON j.rowid=jt.last_row
 LEFT JOIN last_observation a ON a.order_id=o.id AND a.position=1
 LEFT JOIN own_grants pg ON pg.order_id=o.id LEFT JOIN all_grants ag ON ag.user_id=o.user_id AND ag.course_id=o.course_id
 LEFT JOIN payment_facts pf ON pf.order_id=o.id LEFT JOIN refund_operations ro ON ro.order_id=o.id
 LEFT JOIN receipt_totals rt ON rt.order_id=o.id
 LEFT JOIN reconciliation_order_scopes s ON s.order_id=o.id
 LEFT JOIN reconciliation_cases c ON c.order_id=o.id LEFT JOIN users u ON u.id=c.owner_id
), facts AS (
 SELECT *,
 (bad_grant_count>0 OR (status='paid' AND valid_payment_count=0) OR (status='refunded' AND valid_payment_count>payment_revoked)) AS rights_issue,
 json_object('schemaVersion',1,'order',json_object('id',id,'version',version,'status',status,'amount',amount,'currency',currency,'mode',mode,'keyHash',current_key_hash,'providerStatus',provider_status),
 'jobs',json_object('count',job_count,'versions',job_versions,'attempts',attempt_count,'active',active_count),
 'query',json_object('id',observation_id,'code',result_code,'detail',detail_code,'status',observed_status),
 'grants',json_object('count',grant_count,'versions',grant_versions,'last',grant_last,'active',grant_active,'scheduled',grant_scheduled,'expired',grant_expired,'revoked',grant_revoked,
 'paymentCount',payment_count,'paymentVersions',payment_grant_versions,'paymentLast',payment_grant_last,'paymentActive',payment_active,'paymentScheduled',payment_scheduled,'paymentExpired',payment_expired,'paymentRevoked',payment_revoked,'wrongParent',bad_grant_count),
 'financialRecords',json_object('count',payment_event_count,'last',payment_event_last,'refundOperation',refund_operation,'refundVersion',refund_version,'refundState',refund_state),
 'receipts',json_object('count',receipt_count,'authenticated',authenticated_count,'conflicts',receipt_conflicts,'lastConflict',receipt_last_conflict,'lastLink',receipt_last_link),
 'scope',json_object('recorded',scope_recorded,'mode',scope_mode,'merchantHash',scope_merchant,'currentMode',:mode,'currentMerchantHash',:merchant)) AS facts_json
 FROM base
), projected AS (
 SELECT *,
 (case_state IN ('resolved','investigating') AND case_context!=facts_json) AS review_changed,
 (status IN ('pending','confirming','refunding') AND changed_at IS NOT NULL AND :stamp-changed_at>=1800) AS long_pending
 FROM facts
)
"""


def parameters(c, scope, stamp, as_of):
    c.create_function("tpc_hash", 1, lambda value: hashed(value) if isinstance(value, str) else None, deterministic=True)
    return {"mode": scope.mode if scope else None, "merchant": scope.merchant_hash if scope else None,
            "stamp": stamp, "as_of": as_of}


def context_hash(row):
    return hashed(document(json.loads(row["facts_json"])))


def aligned(row):
    if not row["scope_matches"] or row["active_count"] or row["rights_issue"] or row["observation_state"] != "matched" or row["result_code"] != "pg_match":
        return False
    if row["detail_code"] not in {"payment_verified", "payment_current", "access_reconciled", "cancellation_verified", "cancellation_current", "external_cancellation_verified", "unpaid_terminal"}:
        return False  # Older model-only results do not attest to an actual GET.
    if row["current_key_hash"] != row["observed_key_hash"] or row["amount"] != row["observed_amount"] or row["currency"] != row["observed_currency"]:
        return False
    facts = json.loads(row["order_facts"])
    if facts.get("mode") != row["mode"]:
        return False
    return ((row["status"] == "paid" and row["observed_status"] == "DONE") or
            (row["status"] == "refunded" and row["observed_status"] == "CANCELED") or
            (row["status"] == "abandoned" and row["observed_status"] in ("CANCELED", "ABORTED", "EXPIRED") and not row["payment_active"]))


def issues(row):
    result = []
    code, detail = row["result_code"], row["detail_code"]
    if code in ("amount_mismatch", "currency_mismatch"): result.append("amount")
    if code in ("reference_mismatch", "payment_key_mismatch"): result.append("reference")
    if row["rights_issue"]: result.append("permission")
    if row["last_job_state"] == "review" and row["last_job_attempts"] >= row["max_attempts"]: result.append("exhausted")
    if row["active_count"] or row["long_pending"]: result.append("pending")
    if not row["scope_matches"] or code == "scope_mismatch" or detail == "lookup_unavailable": result.append("configuration")
    if detail == "partial_cancellation": result.append("partial")
    if detail == "provider_state_conflict" or row["receipt_conflicts"]: result.append("conflict")
    if row["review_changed"]: result.append("changed")
    return result


def order_view(row):
    totals = {"total": row["valid_payment_count"], "active": row["payment_active"], "scheduled": row["payment_scheduled"], "expired": row["payment_expired"], "revoked": row["payment_revoked"]}
    all_totals = {"total": row["grant_count"], "active": row["grant_active"], "scheduled": row["grant_scheduled"], "expired": row["grant_expired"], "revoked": row["grant_revoked"]}
    return {"kind": "order", "id": row["id"], "courseId": row["course_id"], "title": row["title"], "amount": row["amount"], "currency": row["currency"], "mode": row["mode"],
            "status": row["status"], "version": row["version"], "createdAt": row["created_at"], "updatedAt": row["updated_at"], "paidAt": row["paid_at"],
            "scope": {"recorded": bool(row["scope_recorded"]), "matchesCurrent": bool(row["scope_matches"]), "coverage": "recorded_observations_only"},
            "receipts": {"total": row["receipt_count"], "authenticated": row["authenticated_count"], "conflicts": row["receipt_conflicts"]},
            "access": {"canAccess": row["grant_active"] > 0, "payment": totals, "other": {k: all_totals[k]-totals[k] for k in totals}, "wrongParentCount": row["bad_grant_count"]},
            "query": {"activeCount": row["active_count"], "totalJobs": row["job_count"], "totalAttempts": row["attempt_count"], "nextAt": row["next_at"], "leaseUntil": row["lease_until"],
                      "lastJob": {"id": row["last_job_id"], "state": row["last_job_state"], "version": row["last_job_version"], "attempts": row["last_job_attempts"], "maxAttempts": row["max_attempts"]} if row["last_job_id"] else None,
                      "lastObservation": {"attemptId": row["observation_id"], "jobId": row["observation_job"], "ordinal": row["observation_ordinal"], "state": row["observation_state"], "code": row["result_code"], "detail": row["detail_code"], "providerStatus": row["observed_status"], "amount": row["observed_amount"], "currency": row["observed_currency"], "at": row["finished_at"]} if row["observation_id"] else None},
            "contextHash": context_hash(row), "financialAligned": aligned(row), "issues": issues(row), "longPending": bool(row["long_pending"])}
