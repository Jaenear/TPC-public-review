"""Minimal receipt projection, including an order's current facts when linked."""

import json

from .reconciliation_contract import document, hashed


EVENT_CTE = r"""
, event_base AS (
 SELECT e.rowid AS cursor,e.id,e.mode,e.event_type,e.identity_kind,e.received_at,e.order_reference_hash IS NOT NULL AS has_reference,
 (e.mode=:mode AND e.merchant_hash=:merchant) AS scope_matches,
 a.algorithm,a.verified_at,a.hint_status,a.disposition,
 (SELECT count(*) FROM provider_event_conflicts f WHERE f.event_id=e.id) AS conflict_count,
 (SELECT max(rowid) FROM provider_event_conflicts f WHERE f.event_id=e.id) AS last_conflict,
 l.order_id AS linked_order,l.linked_at,p.title AS order_title,p.facts_json AS linked_facts,
 c.id AS case_id,c.version AS case_version,c.state AS case_state,c.owner_id,c.context_json AS case_context,c.updated_at AS case_updated,
 u.name AS owner_name,u.status AS owner_status,u.role AS owner_role
 FROM provider_events e LEFT JOIN provider_event_auth a ON a.event_id=e.id
 LEFT JOIN provider_event_links l ON l.event_id=e.id LEFT JOIN projected p ON p.id=l.order_id
 LEFT JOIN reconciliation_cases c ON c.event_id=e.id LEFT JOIN users u ON u.id=c.owner_id
), event_facts AS (
 SELECT *,json_object('schemaVersion',1,'event',json_object('id',id,'mode',mode,'type',event_type,'authenticated',algorithm IS NOT NULL,'disposition',disposition,'conflicts',conflict_count,'lastConflict',last_conflict,'hasReference',has_reference,'linkedOrder',linked_order,'linkedAt',linked_at),
 'orderFacts',CASE WHEN linked_facts IS NULL THEN NULL ELSE json(linked_facts) END,
 'scope',json_object('matchesCurrent',scope_matches,'currentMode',:mode,'currentMerchantHash',:merchant)) AS facts_json
 FROM event_base
), event_projected AS (
 SELECT *, (case_state IN ('resolved','investigating') AND case_context!=facts_json) AS review_changed FROM event_facts
)
"""


def event_view(row):
    issues = []
    if row["conflict_count"]: issues.append("conflict")
    if not row["linked_order"]: issues.append("unlinked")
    if not row["algorithm"]: issues.append("authentication")
    if not row["scope_matches"] or row["disposition"] == "scope_mismatch": issues.append("configuration")
    if row["review_changed"]: issues.append("changed")
    return {"kind": "event", "id": row["id"], "mode": row["mode"], "eventType": row["event_type"], "identityKind": row["identity_kind"],
            "receivedAt": row["received_at"], "hasOrderReference": bool(row["has_reference"]),
            "authentication": {"recorded": row["algorithm"] is not None, "algorithm": row["algorithm"], "verifiedAt": row["verified_at"], "disposition": row["disposition"]},
            "hint": row["hint_status"], "scope": {"matchesCurrent": bool(row["scope_matches"]), "coverage": "recorded_observations_only"},
            "linkedOrder": {"id": row["linked_order"], "title": row["order_title"], "linkedAt": row["linked_at"]} if row["linked_order"] else None,
            "conflicts": row["conflict_count"], "issues": issues, "contextHash": hashed(document(json.loads(row["facts_json"])))}
