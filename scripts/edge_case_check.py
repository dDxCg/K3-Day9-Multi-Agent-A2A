"""Deterministic edge-case harness for the policy + verifier stages.

The 50 official cases are a narrow slice of Olist: at most 3 items, at most
3 payments, exactly one seller, and only delivered/canceled/unavailable
statuses. This script feeds the rule engine and the verifier real order ids
from outside that slice — the shapes a hidden test case would most likely
use — and asserts the output schema survives them.

The three LLM agents are bypassed: their reports are reconstructed from the
same tools they call, so a run costs no API credit and is reproducible.

    uv run python scripts/edge_case_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config
from src.data_store import get_order, get_order_items, get_payments
from src.delivery_agent.tools import lookup_delivery_timing
from src.order_and_seller_agent.tools.order_tools import lookup_order_items_and_sellers
from src.payment_agent.tools import lookup_payments, reconcile_with_order_total
from src.policy_agent.agent import _decide
from src.schemas import (
    AffectedEntities,
    Assessment,
    CaseOutput,
    DeliveryReport,
    FinancialResolution,
    OrderSellerReport,
    PaymentReport,
    RankedCause,
    ResponsibleParty,
    RootCauseAnalysis,
)
from src.tracer import tracer
from src.verifier_agent.agent import verify_and_fix
from src.verifier_agent.evidence import MAX_ENTITY_IDS, MAX_EVIDENCE

CASES = [
    ("21-item order", "8272b63d03f5f79c56e9e4120aec44ef"),
    ("29-payment order", "fa65dad1b0e818e3ccc5cb0e39231352"),
    ("multi-seller late delivery", "135b582ac1ec8138d4f9f0ef27a7ba36"),
    ("shipped, never delivered, overdue", "2e7a8482f6fb09756ca50c10d7bfc047"),
    ("canceled with zero payment", "4637ca194b6387e2d538dc89b124b0ee"),
    ("order id absent from orders.csv", "deadbeefdeadbeefdeadbeefdeadbeef"),
]

OPENED_AT = "2018-10-18T00:00:00-03:00"


def _reports(order_id: str) -> tuple[OrderSellerReport, DeliveryReport, PaymentReport]:
    """Rebuild what perfect LLM agents would report, straight from the tools."""
    order = get_order(order_id)
    items = lookup_order_items_and_sellers.invoke({"order_id": order_id})
    timing = lookup_delivery_timing.invoke({"order_id": order_id})
    pay = lookup_payments.invoke({"order_id": order_id})
    recon = reconcile_with_order_total.invoke(
        {
            "order_id": order_id,
            "item_total_brl": items["item_total_brl"],
            "freight_total_brl": items["freight_total_brl"],
        }
    )

    order_seller = OrderSellerReport(
        order_id=order_id,
        order_found=order is not None,
        order_status=order["order_status"] if order else "",
        item_ids=[f"{order_id}:{i['order_item_id']}" for i in items["items"]],
        seller_ids=items["seller_ids"],
        late_seller_ids=items["late_seller_ids"],
        item_total_brl=items["item_total_brl"],
        freight_total_brl=items["freight_total_brl"],
        order_delivered_carrier_date=(
            order["order_delivered_carrier_date"] if order else None
        ),
        evidence_ids=[],
    )
    delivery = DeliveryReport(
        order_id=order_id,
        delivered_late=timing.get("delivered_late", False),
        order_delivered_customer_date=timing.get("order_delivered_customer_date"),
        order_estimated_delivery_date=timing.get("order_estimated_delivery_date"),
        evidence_ids=[],
    )
    payment = PaymentReport(
        order_id=order_id,
        payment_ids=[f"{order_id}:{p['payment_sequential']}" for p in pay["payments"]],
        payment_total_brl=pay["payment_total_brl"],
        payment_count=pay["payment_count"],
        matches_item_freight=recon["matches_item_freight"],
        evidence_ids=[],
    )
    return order_seller, delivery, payment


def _assemble(case_id: str, order_id: str, decision: dict) -> CaseOutput:
    return CaseOutput(
        case_id=case_id,
        assessment=Assessment(
            primary_issue=decision["primary_issue"],
            case_status=decision["case_status"],
            confidence=decision["confidence"],
        ),
        affected_entities=AffectedEntities(order_ids=[order_id]),
        root_cause_analysis=RootCauseAnalysis(
            ranked_causes=[RankedCause(cause_code=decision["cause_code"], rank=1)],
            responsible_parties=[
                ResponsibleParty(**p) for p in decision["responsible_parties"]
            ],
        ),
        evidence_ids=[f"policy:{decision['cause_code']}"],
        financial_resolution=FinancialResolution(),
        resolution_actions=decision["resolution_actions"],
    )


def _check(label: str, order_id: str, case: CaseOutput) -> list[str]:
    failures = []
    ev = case.evidence_ids
    ae = case.affected_entities

    if not any(e.startswith("policy:") for e in ev):
        failures.append("policy: evidence missing")
    if len(ev) > MAX_EVIDENCE:
        failures.append(f"evidence over cap: {len(ev)}")
    for field in ("order_ids", "item_ids", "seller_ids", "payment_ids"):
        if len(getattr(ae, field)) > MAX_ENTITY_IDS:
            failures.append(f"{field} over cap")
    if not 0.0 <= case.assessment.confidence <= 1.0:
        failures.append("confidence out of range")
    if case.assessment.case_status not in ("action_required", "no_action"):
        failures.append("bad case_status")

    refund = case.financial_resolution.recommended_refund_brl
    if case.assessment.case_status == "action_required" and refund <= 0:
        failures.append("action_required with no refund")
    if case.assessment.case_status == "no_action" and refund != 0:
        failures.append("no_action with a refund")

    if get_order(order_id) is None and ae.order_ids:
        failures.append("emitted an order id that is not in orders.csv")
    if get_order(order_id) is not None:
        # Payment evidence must survive truncation whenever it fits.
        n_pay = len(get_payments(order_id))
        if n_pay and len(ev) < MAX_EVIDENCE:
            if not any(e.startswith("payment:") for e in ev):
                failures.append("payment evidence dropped while under the cap")
    return failures


def main() -> int:
    tracer.path = Path(__file__).resolve().parent.parent / "logging" / "edge_trace.jsonl"
    tracer.start_run()

    total_failures = 0
    for label, order_id in CASES:
        tracer.set_case(label)
        order_seller, delivery, payment = _reports(order_id)
        decision = _decide(order_seller, delivery, payment, OPENED_AT)
        case = verify_and_fix(_assemble(label, order_id, decision), order_id, decision)
        failures = _check(label, order_id, case)
        total_failures += len(failures)

        n_items = len(get_order_items(order_id))
        n_pays = len(get_payments(order_id))
        print(f"\n=== {label} ({order_id[:12]}…) items={n_items} payments={n_pays}")
        print(f"    issue      : {case.assessment.primary_issue} "
              f"[{case.assessment.case_status}] conf={case.assessment.confidence}")
        print(f"    cause      : {case.root_cause_analysis.ranked_causes[0].cause_code}")
        print(f"    parties    : "
              f"{[p.party_id for p in case.root_cause_analysis.responsible_parties]}")
        print(f"    entities   : items={len(case.affected_entities.item_ids)} "
              f"sellers={len(case.affected_entities.seller_ids)} "
              f"payments={len(case.affected_entities.payment_ids)}")
        print(f"    evidence   : {len(case.evidence_ids)} -> {case.evidence_ids}")
        print(f"    refund     : {case.financial_resolution.recommended_refund_brl} BRL")
        print("    FAIL: " + "; ".join(failures) if failures else "    OK")

    print(f"\nEVIDENCE_MODE={Config.EVIDENCE_MODE}  total failures: {total_failures}")
    return 1 if total_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
