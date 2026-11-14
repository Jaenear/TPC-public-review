"""A single stable cursor across actual receipt, work and business observations."""

import base64
import json
import re

from ..core import ApiError
from .reconciliation_contract import PG_STATUSES


def cursor_encode(row):
    raw = json.dumps([row["sort_at"], row["rank"], row["sequence"]], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def cursor_decode(value):
    if value is None:
        return (253402300800, 9, 2**63-1)
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{4,128}", value):
            raise ValueError()
        parts = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if (not isinstance(parts, list) or len(parts) != 3 or any(type(n) is not int for n in parts)
                or not -62135596800 <= parts[0] <= 253402300800 or not 0 <= parts[1] <= 9 or not -2**63 <= parts[2] <= 2**63-1):
            raise ValueError()
        return tuple(parts)
    except (ValueError, TypeError, UnicodeError):
        raise ApiError(400, "시간선의 이전 페이지를 다시 확인해주세요.") from None


ORDER_TIMELINE = r"""
SELECT 'scope' AS kind,s.observed_at AS recorded_at,0 AS rank,s.rowid AS sequence,s.id AS id,
 json_object('origin',s.origin,'orderVersion',s.order_version,'operator',CASE WHEN s.origin='operator' THEN u.name ELSE NULL END) AS payload
 FROM reconciliation_scope_observations s LEFT JOIN users u ON u.id=s.actor_id WHERE s.order_id=:id
UNION ALL
SELECT 'receipt',e.received_at,1,e.rowid,e.id,
 json_object('eventType',e.event_type,'identityKind',e.identity_kind,'authenticated',a.event_id IS NOT NULL,'algorithm',a.algorithm,'hint',a.hint_status,'disposition',a.disposition,'conflicts',(SELECT count(*) FROM provider_event_conflicts WHERE event_id=e.id))
 FROM provider_events e JOIN provider_event_links l ON l.event_id=e.id LEFT JOIN provider_event_auth a ON a.event_id=e.id WHERE l.order_id=:id
UNION ALL
SELECT 'link',l.linked_at,2,l.rowid,l.event_id,
 json_object('eventId',l.event_id,'orderId',l.order_id,'source',CASE WHEN l.linked_by IS NOT NULL THEN 'operator_observation' WHEN EXISTS(SELECT 1 FROM provider_event_auth WHERE event_id=l.event_id) THEN 'authenticated_attachment' ELSE 'unrecorded_attachment' END)
 FROM provider_event_links l WHERE l.order_id=:id
UNION ALL
SELECT 'work',j.created_at,3,j.rowid,j.id,
 json_object('jobId',j.id,'source',j.source,'eventId',j.event_id,'phase',s.phase,'referenceId',s.reference_id,'reason',cmd.reason,'operator',u.name,'policyVersion',j.policy_version,'maxAttempts',j.max_attempts)
 FROM reconciliation_jobs j LEFT JOIN reconciliation_schedules s ON s.job_id=j.id LEFT JOIN reconciliation_commands cmd ON cmd.job_id=j.id
 LEFT JOIN users u ON u.id=cmd.actor_id WHERE j.order_id=:id
UNION ALL
SELECT 'query_started',a.started_at,4,a.rowid,a.id,
 json_object('attemptId',a.id,'jobId',a.job_id,'ordinal',a.ordinal,'leaseUntil',a.lease_until)
 FROM reconciliation_attempts a JOIN reconciliation_jobs j ON j.id=a.job_id WHERE j.order_id=:id
UNION ALL
SELECT 'query_finished',a.finished_at,5,a.rowid,a.id,
 json_object('attemptId',a.id,'jobId',a.job_id,'ordinal',a.ordinal,'state',a.state,'code',a.result_code,'detail',o.detail_code,'providerStatus',a.observed_status,'amount',a.observed_amount,'currency',a.observed_currency)
 FROM reconciliation_attempts a JOIN reconciliation_jobs j ON j.id=a.job_id LEFT JOIN reconciliation_outcomes o ON o.attempt_id=a.id WHERE j.order_id=:id AND a.finished_at IS NOT NULL
UNION ALL
SELECT 'financial_record',CASE WHEN p.created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*' AND CAST(substr(p.created_at,1,4) AS INTEGER) BETWEEN 1 AND 9999 THEN CAST(strftime('%s',p.created_at) AS INTEGER) END,6,p.rowid,p.id,
 json_object('type',p.event_type,'state',p.status,'providerStatus',p.provider_status)
 FROM payment_events p WHERE p.order_id=:id
UNION ALL
SELECT 'operator_review',e.created_at,7,e.rowid,e.id,
 json_object('caseId',e.case_id,'version',e.version,'action',e.action,'state',e.state,'note',e.note,'operator',u.name,'ownerId',e.owner_id)
 FROM reconciliation_case_events e JOIN reconciliation_cases c ON c.id=e.case_id LEFT JOIN users u ON u.id=e.actor_id
 WHERE c.order_id=:id
"""

EVENT_TIMELINE = r"""
SELECT 'receipt' AS kind,e.received_at AS recorded_at,1 AS rank,e.rowid AS sequence,e.id AS id,
 json_object('eventType',e.event_type,'identityKind',e.identity_kind,'authenticated',a.event_id IS NOT NULL,'algorithm',a.algorithm,'hint',a.hint_status,'disposition',a.disposition)
 AS payload FROM provider_events e LEFT JOIN provider_event_auth a ON a.event_id=e.id WHERE e.id=:id
UNION ALL
SELECT 'link',l.linked_at,2,l.rowid,l.event_id,json_object('eventId',l.event_id,'orderId',l.order_id,'source',CASE WHEN l.linked_by IS NOT NULL THEN 'operator_observation' WHEN EXISTS(SELECT 1 FROM provider_event_auth WHERE event_id=l.event_id) THEN 'authenticated_attachment' ELSE 'unrecorded_attachment' END)
 FROM provider_event_links l WHERE l.event_id=:id
UNION ALL
SELECT 'content_conflict',f.observed_at,3,f.rowid,f.id,json_object('originalEventId',f.event_id)
 FROM provider_event_conflicts f WHERE f.event_id=:id
UNION ALL
SELECT 'operator_review',e.created_at,7,e.rowid,e.id,
 json_object('caseId',e.case_id,'version',e.version,'action',e.action,'state',e.state,'note',e.note,'operator',u.name,'ownerId',e.owner_id)
 FROM reconciliation_case_events e JOIN reconciliation_cases c ON c.id=e.case_id LEFT JOIN users u ON u.id=e.actor_id WHERE c.event_id=:id
"""


def timeline(c, kind, subject, query, clean):
    if set(query) - {"before", "limit"} or any(len(v) != 1 for v in query.values()):
        raise ApiError(400, "시간선 페이지를 확인해주세요.")
    value = query.get("limit", ["30"])[0]
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,2}", value) or not 1 <= int(value) <= 50:
        raise ApiError(400, "시간선은 한 번에1–50개씩 확인할 수 있습니다.")
    limit = int(value)
    t, rank, sequence = cursor_decode(query.get("before", [None])[0])
    sql = ORDER_TIMELINE if kind == "order" else EVENT_TIMELINE
    rows = c.execute("WITH records AS ("+sql+"), ordered AS (SELECT *,COALESCE(recorded_at,0) AS sort_at FROM records) SELECT * FROM ordered WHERE (sort_at,rank,sequence)<(:t,:rank,:sequence) ORDER BY sort_at DESC,rank DESC,sequence DESC LIMIT :limit", {"id": subject, "t": t, "rank": rank, "sequence": sequence, "limit": limit+1}).fetchall()
    items = []
    for row in rows[:limit]:
        payload = json.loads(row["payload"])
        if "providerStatus" in payload and payload["providerStatus"] not in PG_STATUSES:
            payload["providerStatus"] = None
        for key in ("reason", "note"):
            if payload.get(key): payload[key] = clean(payload[key])
        items.append({"id": row["kind"]+":"+row["id"], "kind": row["kind"], "recordedAt": row["recorded_at"], "facts": payload})
    return {"items": items, "page": {"limit": limit, "nextBefore": cursor_encode(rows[limit-1]) if len(rows)>limit else None},
            "coverage": "recorded_observations_only", "ordering": "server_recorded_time_then_source_and_rowid"}
