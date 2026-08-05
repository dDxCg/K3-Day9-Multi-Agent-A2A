"""Policy Agent: applies EC_POLICY_V1 deterministically.

The rule table (README section 4) is an exact, ordered lookup with no
room for interpretation, so this agent is a plain rule engine rather than
an LLM call — it consumes the structured reports handed off by the other
three agents and returns a policy decision, still framed as an A2A
message so the pipeline stays uniform.
"""

from __future__ import annotations

from src.a2a_protocol import A2AMessage
from src.schemas import DeliveryReport, OrderSellerReport, PaymentReport
from src.tracer import tracer

AGENT_NAME = "policy_agent"

PLATFORM_PARTY = {"party_type": "platform", "party_id": "OLIST_PLATFORM"}
LOGISTICS_PARTY = {"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}


def _decide(
    order_seller: OrderSellerReport, delivery: DeliveryReport, payment: PaymentReport
) -> dict:
    if order_seller.order_status == "canceled" and payment.payment_total_brl > 0:
        return {
            "primary_issue": "canceled_order_paid",
            "case_status": "action_required",
            "cause_code": "ORDER_CANCELED_AFTER_PAYMENT",
            "responsible_parties": [PLATFORM_PARTY],
            "recommended_refund_brl": payment.payment_total_brl,
            "resolution_actions": ["issue_full_refund"],
            "confidence": 0.95,
        }

    if order_seller.order_status == "unavailable" and payment.payment_total_brl > 0:
        return {
            "primary_issue": "unavailable_order_paid",
            "case_status": "action_required",
            "cause_code": "ORDER_UNAVAILABLE_AFTER_PAYMENT",
            "responsible_parties": [PLATFORM_PARTY],
            "recommended_refund_brl": payment.payment_total_brl,
            "resolution_actions": ["issue_full_refund"],
            "confidence": 0.95,
        }

    if delivery.delivered_late and order_seller.late_seller_ids:
        parties = [
            {"party_type": "seller", "party_id": sid}
            for sid in order_seller.late_seller_ids[:3]
        ]
        return {
            "primary_issue": "late_delivery_seller",
            "case_status": "action_required",
            "cause_code": "SELLER_HANDOFF_AFTER_LIMIT",
            "responsible_parties": parties,
            "recommended_refund_brl": order_seller.freight_total_brl,
            "resolution_actions": ["refund_freight"],
            "confidence": 0.9,
        }

    if delivery.delivered_late and not order_seller.late_seller_ids:
        return {
            "primary_issue": "late_delivery_logistics",
            "case_status": "action_required",
            "cause_code": "CARRIER_DELIVERED_AFTER_ESTIMATE",
            "responsible_parties": [LOGISTICS_PARTY],
            "recommended_refund_brl": order_seller.freight_total_brl,
            "resolution_actions": ["refund_freight"],
            "confidence": 0.9,
        }

    if payment.payment_count >= 2 and payment.matches_item_freight:
        return {
            "primary_issue": "valid_split_payment",
            "case_status": "no_action",
            "cause_code": "MULTIPLE_PAYMENTS_RECONCILED",
            "responsible_parties": [],
            "recommended_refund_brl": 0.0,
            "resolution_actions": ["explain_valid_split_payment"],
            "confidence": 0.9,
        }

    if not delivery.delivered_late and payment.matches_item_freight:
        return {
            "primary_issue": "unsupported_late_claim",
            "case_status": "no_action",
            "cause_code": "DELIVERY_WITHIN_ESTIMATE",
            "responsible_parties": [],
            "recommended_refund_brl": 0.0,
            "resolution_actions": ["reject_late_refund"],
            "confidence": 0.9,
        }

    # Should not happen on the official 50-case set (README guarantees no
    # ambiguous multi-seller / unmatched-rule cases); conservative fallback.
    return {
        "primary_issue": "unsupported_late_claim",
        "case_status": "no_action",
        "cause_code": "DELIVERY_WITHIN_ESTIMATE",
        "responsible_parties": [],
        "recommended_refund_brl": 0.0,
        "resolution_actions": ["reject_late_refund"],
        "confidence": 0.3,
    }


def run(
    task_id: str,
    order_seller: OrderSellerReport,
    delivery: DeliveryReport,
    payment: PaymentReport,
) -> A2AMessage:
    decision = _decide(order_seller, delivery, payment)
    tracer.log("policy_decision", agent=AGENT_NAME, decision=decision)
    response = A2AMessage(
        task_id=task_id,
        from_agent=AGENT_NAME,
        to_agent="coordinator_agent",
        role="response",
        data=decision,
        evidence_ids=[f"policy:{decision['cause_code']}"],
    )
    tracer.log_a2a("send", AGENT_NAME, response)
    return response
