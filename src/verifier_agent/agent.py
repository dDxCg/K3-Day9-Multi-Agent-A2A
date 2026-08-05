"""Verifier Agent: checks evidence ids, amounts and schema limits before
a case is written to output/. Deterministic (no LLM) — it re-derives
ground truth straight from the CSVs and corrects/drops anything that
doesn't match, logging every correction to trace.jsonl.
"""

from __future__ import annotations

from src.data_store import get_order, get_order_items, get_payments, get_seller
from src.schemas import CaseOutput
from src.tracer import tracer

AGENT_NAME = "verifier_agent"

KNOWN_CAUSE_CODES = {
    "SELLER_HANDOFF_AFTER_LIMIT",
    "CARRIER_DELIVERED_AFTER_ESTIMATE",
    "ORDER_CANCELED_AFTER_PAYMENT",
    "ORDER_UNAVAILABLE_AFTER_PAYMENT",
    "MULTIPLE_PAYMENTS_RECONCILED",
    "DELIVERY_WITHIN_ESTIMATE",
}

MAX_ENTITY_IDS = 5
MAX_EVIDENCE = 10
MAX_CAUSES = 3
MAX_PARTIES = 3
MAX_ACTIONS = 5


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


def verify_and_fix(case: CaseOutput) -> CaseOutput:
    corrections: list[str] = []

    valid_evidence = [e for e in case.evidence_ids if _valid_evidence(e)]
    dropped = set(case.evidence_ids) - set(valid_evidence)
    if dropped:
        corrections.append(f"dropped invalid evidence_ids: {sorted(dropped)}")
    case.evidence_ids = valid_evidence[:MAX_EVIDENCE]

    for field_name in ("order_ids", "item_ids", "seller_ids", "payment_ids"):
        values = getattr(case.affected_entities, field_name)
        if len(values) > MAX_ENTITY_IDS:
            corrections.append(f"truncated {field_name} to {MAX_ENTITY_IDS}")
        setattr(case.affected_entities, field_name, values[:MAX_ENTITY_IDS])

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

    fin = case.financial_resolution
    order_ids = case.affected_entities.order_ids
    if order_ids:
        order_id = order_ids[0]
        items = get_order_items(order_id)
        payments = get_payments(order_id)
        true_item_total = round(sum(float(i["price"]) for i in items), 2)
        true_freight_total = round(sum(float(i["freight_value"]) for i in items), 2)
        true_payment_total = round(sum(float(p["payment_value"]) for p in payments), 2)

        for field_name, true_value in (
            ("item_total_brl", true_item_total),
            ("freight_total_brl", true_freight_total),
            ("payment_total_brl", true_payment_total),
        ):
            current = getattr(fin, field_name)
            if abs(current - true_value) > 0.01:
                corrections.append(f"corrected {field_name}: {current} -> {true_value}")
                setattr(fin, field_name, true_value)

    fin.recommended_refund_brl = round(fin.recommended_refund_brl, 2)

    if case.assessment.case_status not in ("action_required", "no_action"):
        corrections.append("fixed invalid case_status")
        case.assessment.case_status = (
            "action_required" if fin.recommended_refund_brl > 0 else "no_action"
        )

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
