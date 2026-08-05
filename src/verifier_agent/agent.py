"""Verifier Agent: last gate before a case is written to output/.

Deterministic (no LLM). It re-derives every fact it can straight from the
CSVs — affected entities, evidence IDs, money — so an under-reporting or
mis-copying agent upstream cannot leak into the submission, and logs every
correction it makes to trace.jsonl.
"""

from __future__ import annotations

from typing import Any

from src.config import Config
from src.data_store import get_order, get_order_items, get_payments, get_seller
from src.schemas import AffectedEntities, CaseOutput
from src.tracer import tracer
from src.verifier_agent.evidence import (
    MAX_ACTIONS,
    MAX_CAUSES,
    MAX_EVIDENCE,
    MAX_PARTIES,
    build_entities,
    build_evidence,
    collect_facts,
)

AGENT_NAME = "verifier_agent"

KNOWN_CAUSE_CODES = {
    "SELLER_HANDOFF_AFTER_LIMIT",
    "CARRIER_DELIVERED_AFTER_ESTIMATE",
    "ORDER_CANCELED_AFTER_PAYMENT",
    "ORDER_UNAVAILABLE_AFTER_PAYMENT",
    "MULTIPLE_PAYMENTS_RECONCILED",
    "DELIVERY_WITHIN_ESTIMATE",
}

_FULL_REFUND_ISSUES = {"canceled_order_paid", "unavailable_order_paid"}
_FREIGHT_REFUND_ISSUES = {"late_delivery_seller", "late_delivery_logistics"}


def _valid_evidence(evidence_id: str) -> bool:
    parts = evidence_id.split(":")
    kind = parts[0] if parts else ""

    if kind == "order" and len(parts) == 2:
        return get_order(parts[1]) is not None

    if kind == "item" and len(parts) == 3:
        order_id, item_id = parts[1], parts[2]
        return any(
            str(row["order_item_id"]) == item_id for row in get_order_items(order_id)
        )

    if kind == "payment" and len(parts) == 3:
        order_id, seq = parts[1], parts[2]
        return any(
            str(row["payment_sequential"]) == seq for row in get_payments(order_id)
        )

    if kind == "seller" and len(parts) == 2:
        return get_seller(parts[1]) is not None

    if kind == "policy" and len(parts) == 2:
        return parts[1] in KNOWN_CAUSE_CODES

    return False


def verify_and_fix(
    case: CaseOutput, order_id: str, decision: dict[str, Any]
) -> CaseOutput:
    corrections: list[str] = []
    facts = collect_facts(order_id, decision)

    # --- entities and evidence: rebuilt from the CSVs, not from the agents ---
    rebuilt_entities = build_entities(order_id, facts)
    reported_entities = case.affected_entities.model_dump()
    changed = {
        field: (reported_entities[field], values)
        for field, values in rebuilt_entities.items()
        if set(reported_entities[field]) != set(values)
    }
    if changed:
        corrections.append(f"rebuilt affected_entities from CSV: {changed}")
    case.affected_entities = AffectedEntities(**rebuilt_entities)

    rebuilt_evidence = build_evidence(order_id, decision, facts, Config.EVIDENCE_MODE)
    invalid = [e for e in rebuilt_evidence if not _valid_evidence(e)]
    if invalid:
        corrections.append(f"dropped invalid evidence_ids: {invalid}")
        rebuilt_evidence = [e for e in rebuilt_evidence if e not in invalid]
    if set(rebuilt_evidence) != set(case.evidence_ids):
        corrections.append(
            f"rebuilt evidence_ids from CSV: {case.evidence_ids} -> {rebuilt_evidence}"
        )
    case.evidence_ids = rebuilt_evidence[:MAX_EVIDENCE]

    # --- schema limits ---
    if len(case.root_cause_analysis.ranked_causes) > MAX_CAUSES:
        corrections.append("truncated ranked_causes")
        case.root_cause_analysis.ranked_causes = case.root_cause_analysis.ranked_causes[
            :MAX_CAUSES
        ]
    if len(case.root_cause_analysis.responsible_parties) > MAX_PARTIES:
        corrections.append("truncated responsible_parties")
        case.root_cause_analysis.responsible_parties = (
            case.root_cause_analysis.responsible_parties[:MAX_PARTIES]
        )
    if len(case.resolution_actions) > MAX_ACTIONS:
        corrections.append("truncated resolution_actions")
        case.resolution_actions = case.resolution_actions[:MAX_ACTIONS]

    clamped = min(1.0, max(0.0, case.assessment.confidence))
    if clamped != case.assessment.confidence:
        corrections.append("clamped confidence to [0,1]")
        case.assessment.confidence = clamped

    # --- money: recomputed from the CSVs, refund re-derived from the totals ---
    fin = case.financial_resolution
    items = get_order_items(order_id)
    payments = get_payments(order_id)
    totals = {
        "item_total_brl": round(sum(float(i["price"]) for i in items), 2),
        "freight_total_brl": round(sum(float(i["freight_value"]) for i in items), 2),
        "payment_total_brl": round(sum(float(p["payment_value"]) for p in payments), 2),
    }
    for field_name, true_value in totals.items():
        current = getattr(fin, field_name)
        if abs(current - true_value) > 0.01:
            corrections.append(f"corrected {field_name}: {current} -> {true_value}")
            setattr(fin, field_name, true_value)

    issue = case.assessment.primary_issue
    if issue in _FULL_REFUND_ISSUES:
        expected_refund = fin.payment_total_brl
    elif issue in _FREIGHT_REFUND_ISSUES:
        expected_refund = fin.freight_total_brl
    else:
        expected_refund = 0.0
    if abs(fin.recommended_refund_brl - expected_refund) > 0.01:
        corrections.append(
            f"corrected recommended_refund_brl: {fin.recommended_refund_brl} "
            f"-> {expected_refund}"
        )
    fin.recommended_refund_brl = round(expected_refund, 2)

    expected_status = "action_required" if fin.recommended_refund_brl > 0 else "no_action"
    if case.assessment.case_status != expected_status:
        corrections.append(
            f"corrected case_status: {case.assessment.case_status} -> {expected_status}"
        )
        case.assessment.case_status = expected_status

    if corrections:
        tracer.log(
            "verifier_corrections",
            agent=AGENT_NAME,
            case_id=case.case_id,
            corrections=corrections,
        )
    else:
        tracer.log("verifier_ok", agent=AGENT_NAME, case_id=case.case_id)

    return case
