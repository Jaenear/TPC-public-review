"""Server-filtered bounded queues; filtering never happens after pagination."""

import re

from ..core import ApiError
from .reconciliation_desk_facts import ORDER_CTE, order_view
from .reconciliation_desk_events import EVENT_CTE, event_view

ALIGNED = """(scope_matches AND active_count=0 AND rights_issue=0 AND observation_state='matched' AND result_code='pg_match'
 AND detail_code IN ('payment_verified','payment_current','access_reconciled','cancellation_verified','cancellation_current','external_cancellation_verified','unpaid_terminal')
 AND current_key_hash=observed_key_hash AND amount=observed_amount AND currency=observed_currency AND json_extract(order_facts,'$.mode')=mode
 AND ((status='paid' AND observed_status='DONE') OR (status='refunded' AND observed_status='CANCELED') OR (status='abandoned' AND observed_status IN ('CANCELED','ABORTED','EXPIRED') AND payment_active=0)))"""
ATTENTION = "(last_job_state IN ('queued','running','retry','review') OR rights_issue OR long_pending OR review_changed OR receipt_conflicts>0 OR case_state IN ('open','investigating'))"
ORDER_ISSUES = {
    "amount": "result_code IN ('amount_mismatch','currency_mismatch')",
    "reference": "result_code IN ('reference_mismatch','payment_key_mismatch')",
    "permission": "rights_issue", "exhausted": "last_job_state='review' AND last_job_attempts>=max_attempts",
    "pending": "active_count>0 OR long_pending", "configuration": "COALESCE(scope_matches,0)=0 OR result_code='scope_mismatch' OR detail_code='lookup_unavailable'",
    "partial": "detail_code='partial_cancellation'", "conflict": "detail_code='provider_state_conflict' OR receipt_conflicts>0", "changed": "review_changed",
    "unlinked": "0", "authentication": "0",
}
EVENT_ISSUES = {"conflict": "conflict_count>0", "unlinked": "linked_order IS NULL", "authentication": "algorithm IS NULL", "configuration": "COALESCE(scope_matches,0)=0 OR disposition='scope_mismatch'", "changed": "review_changed"}


def queue_filters(query):
    if set(query)-{"tab", "mode", "issue", "review", "owner", "q", "before", "limit"} or any(len(v)!=1 for v in query.values()):
        raise ApiError(400, "대사 목록의 필터와 페이지를 확인해주세요.")
    p = {k: v[0] for k,v in query.items()}
    p.setdefault("tab", "attention");p.setdefault("mode", "all");p.setdefault("issue", "all");p.setdefault("review", "all");p.setdefault("q", "")
    p.setdefault("owner", "all")
    if (p["tab"] not in ("attention", "active", "matched", "events", "mine", "all") or p["mode"] not in ("all", "test", "live")
            or p["issue"] not in {"all", *ORDER_ISSUES} or p["review"] not in ("all", "unassigned", "open", "investigating", "resolved", "changed")
            or p["owner"] not in ("all", "mine", "unassigned")
            or not isinstance(p["q"],str) or (p["q"] and not re.fullmatch(r"[A-Za-z0-9_-]{1,120}",p["q"]))):
        raise ApiError(400, "대사 목록의 환경·상태·정확한 내부 ID를 확인해주세요.")
    limit = p.get("limit", "30");before=p.get("before")
    if not re.fullmatch(r"[0-9]{1,2}",limit) or not 1<=int(limit)<=50 or (before is not None and (not re.fullmatch(r"-?[0-9]{1,19}",before) or not -2**63<=int(before)<=2**63-1)):
        raise ApiError(400, "대사 목록은 한 번에1–50개씩 확인할 수 있습니다.")
    p["limit"]=int(limit);p["before"]=int(before) if before else None
    return p


def where(filters, event=False):
    clauses = ["(:filter_mode='all' OR mode=:filter_mode)", "(:search='' OR id=:search)"]
    if filters["owner"] == "mine": clauses.append("owner_id=:actor")
    elif filters["owner"] == "unassigned": clauses.append("owner_id IS NULL")
    if not event: clauses.append("(job_count>0 OR case_id IS NOT NULL OR status IN ('pending','confirming','refunding') OR rights_issue OR :search!='')")
    issue = filters["issue"]
    if issue != "all": clauses.append("("+(EVENT_ISSUES.get(issue,"0") if event else ORDER_ISSUES[issue])+")")
    review=filters["review"]
    if review=="unassigned":clauses.append("owner_id IS NULL")
    elif review=="changed":clauses.append("review_changed")
    elif review!="all":clauses.append("case_state=:review")
    return " AND ".join(clauses)


def queue(c, filters, parameters, actor_id, case_view):
    params={**parameters,"filter_mode":filters["mode"],"search":filters["q"],"review":filters["review"],"actor":actor_id,"before":filters["before"],"limit":filters["limit"]+1}
    base=where(filters);event_base=where(filters,True)
    count=c.execute(ORDER_CTE+" SELECT count(*) AS total,COALESCE(sum("+ATTENTION+"),0) AS attention,COALESCE(sum(active_count>0),0) AS active,COALESCE(sum("+ALIGNED+"),0) AS matched,COALESCE(sum(owner_id=:actor),0) AS mine FROM projected WHERE "+base,params).fetchone()
    events=c.execute(ORDER_CTE+EVENT_CTE+" SELECT count(*) AS total FROM event_projected WHERE "+event_base+" AND (linked_order IS NULL OR conflict_count>0 OR case_id IS NOT NULL)",params).fetchone()[0]
    counts={**dict(count),"events":events}
    tab=filters["tab"];is_event=tab=="events"
    sql=ORDER_CTE+(EVENT_CTE if is_event else "")+" SELECT * FROM "+("event_projected" if is_event else "projected")+" WHERE "+(event_base if is_event else base)
    predicate={"attention":ATTENTION,"active":"active_count>0","matched":ALIGNED,"mine":"owner_id=:actor","events":"linked_order IS NULL OR conflict_count>0 OR case_id IS NOT NULL","all":"1"}[tab]
    sql+=" AND ("+predicate+") AND (:before IS NULL OR cursor<:before) ORDER BY cursor DESC LIMIT :limit"
    rows=c.execute(sql,params).fetchall();items=[]
    for row in rows[:filters["limit"]]:
        value=event_view(row) if is_event else order_view(row)
        value["case"]=case_view(row,actor_id)
        items.append(value)
    return {"items":items,"counts":counts,"filters":{k:v for k,v in filters.items() if k!="before"},
            "page":{"limit":filters["limit"],"nextBefore":str(rows[filters["limit"]-1]["cursor"]) if len(rows)>filters["limit"] else None},
            "countScope":"current_environment_issue_review_and_exact_id_filters_before_tab"}
