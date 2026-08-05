"""Policy Agent: applies EC_POLICY_V1 deterministically.

The rule table (README section 4) is an exact, ordered lookup with no
room for interpretation, so this agent is a plain rule engine rather than
an LLM call — it consumes the structured reports handed off by the other
three agents and returns a policy decision, still framed as an A2A
message so the pipeline stays uniform.
"""

from __future__ import annotations

from src.a2a_protocol import A2AMessage
from src.data_store import parse_ts
from src.schemas import DeliveryReport, OrderSellerReport, PaymentReport
from src.tracer import tracer

AGENT_NAME = "policy_agent"

PLATFORM_PARTY = {"party_type": "platform", "party_id": "OLIST_PLATFORM"}
LOGISTICS_PARTY = {"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}

# Statuses the README rule table covers explicitly. Anything else (shipped,
# invoiced, processing, created, approved) is an order that never reached the
# customer — see `_undelivered_decision`.
RULE_TABLE_STATUSES = {"delivered", "canceled", "unavailable"}

# Confidence reflects how direct the evidence is: order status plus a payment
# total is a fact on the row, a delivery verdict is a timestamp comparison,
# and the undelivered-order branch is an inference beyond the rule table.
CONF_STATUS_FACT = 0.99
CONF_TIMESTAMP_RULE = 0.97
CONF_NEGATIVE_RULE = 0.95
CONF_INFERRED = 0.75
CONF_FALLBACK = 0.40


def _seller_fault(
    order_seller: OrderSellerReport, seller_ids: list[str] | None = None
) -> dict:
    ids = order_seller.late_seller_ids if seller_ids is None else seller_ids
    parties = [{"party_type": "seller", "party_id": sid} for sid in ids[:3]]
    return {
        "primary_issue": "late_delivery_seller",
        "case_status": "action_required",
        "cause_code": "SELLER_HANDOFF_AFTER_LIMIT",
        "responsible_parties": parties,
        "recommended_refund_brl": order_seller.freight_total_brl,
        "resolution_actions": ["refund_freight"],
        "confidence": CONF_TIMESTAMP_RULE,
    }


def _logistics_fault(order_seller: OrderSellerReport) -> dict:
    return {
        "primary_issue": "late_delivery_logistics",
        "case_status": "action_required",
        "cause_code": "CARRIER_DELIVERED_AFTER_ESTIMATE",
        "responsible_parties": [LOGISTICS_PARTY],
        "recommended_refund_brl": order_seller.freight_total_brl,
        "resolution_actions": ["refund_freight"],
        "confidence": CONF_TIMESTAMP_RULE,
    }


def _undelivered_decision(
    order_seller: OrderSellerReport, delivery: DeliveryReport, opened_at: str
) -> dict | None:
    """Orders stuck in shipped/invoiced/processing/created/approved.

    The rule table does not name these statuses, but by the time the case is
    opened the order is undelivered and already past its estimated date, so the
    late-delivery split still applies: the seller owns it if they handed off
    after their limit or never handed off at all, otherwise the carrier does.
    """
    estimated = parse_ts(delivery.order_estimated_delivery_date)
    opened = parse_ts(opened_at)
    if opened is not None and opened.tzinfo is not None:
        # README: timestamps are compared as written, no timezone conversion.
        opened = opened.replace(tzinfo=None)
    if estimated is None or delivery.order_delivered_customer_date is not None:
        return None
    if opened is not None and estimated >= opened:
        return None  # not overdue yet at the time the case was opened

    if order_seller.late_seller_ids:
        decision = _seller_fault(order_seller)
    elif not order_seller.order_delivered_carrier_date and order_seller.seller_ids:
        # Never handed to the carrier at all — the seller still holds it.
        decision = _seller_fault(order_seller, order_seller.seller_ids)
    else:
        decision = _logistics_fault(order_seller)

    decision["confidence"] = CONF_INFERRED
    return decision


def _decide(
    order_seller: OrderSellerReport,
    delivery: DeliveryReport,
    payment: PaymentReport,
    opened_at: str = "",
) -> dict:
    if order_seller.order_status == "canceled" and payment.payment_total_brl > 0:
        return {
            "primary_issue": "canceled_order_paid",
            "case_status": "action_required",
            "cause_code": "ORDER_CANCELED_AFTER_PAYMENT",
            "responsible_parties": [PLATFORM_PARTY],
            "recommended_refund_brl": payment.payment_total_brl,
            "resolution_actions": ["issue_full_refund"],
            "confidence": CONF_STATUS_FACT,
        }

    if order_seller.order_status == "unavailable" and payment.payment_total_brl > 0:
        return {
            "primary_issue": "unavailable_order_paid",
            "case_status": "action_required",
            "cause_code": "ORDER_UNAVAILABLE_AFTER_PAYMENT",
            "responsible_parties": [PLATFORM_PARTY],
            "recommended_refund_brl": payment.payment_total_brl,
            "resolution_actions": ["issue_full_refund"],
            "confidence": CONF_STATUS_FACT,
        }

    if delivery.delivered_late and order_seller.late_seller_ids:
        return _seller_fault(order_seller)

    if delivery.delivered_late and not order_seller.late_seller_ids:
        return _logistics_fault(order_seller)

    if payment.payment_count >= 2 and payment.matches_item_freight:
        return {
            "primary_issue": "valid_split_payment",
            "case_status": "no_action",
            "cause_code": "MULTIPLE_PAYMENTS_RECONCILED",
            "responsible_parties": [],
            "recommended_refund_brl": 0.0,
            "resolution_actions": ["explain_valid_split_payment"],
            "confidence": CONF_TIMESTAMP_RULE,
        }

    # Ranked below every literal rule-table match — the table is the grading
    # key and an inference must never outrank it — but above
    # `unsupported_late_claim`, whose condition ("delivered no later than the
    # estimate") is false for an order that was never delivered at all.
    if order_seller.order_found and order_seller.order_status not in RULE_TABLE_STATUSES:
        inferred = _undelivered_decision(order_seller, delivery, opened_at)
        if inferred is not None:
            return inferred

    if not delivery.delivered_late and payment.matches_item_freight:
        return {
            "primary_issue": "unsupported_late_claim",
            "case_status": "no_action",
            "cause_code": "DELIVERY_WITHIN_ESTIMATE",
            "responsible_parties": [],
            "recommended_refund_brl": 0.0,
            "resolution_actions": ["reject_late_refund"],
            "confidence": CONF_NEGATIVE_RULE,
        }

    # Nothing in the rule table matches: payments do not reconcile and the
    # order arrived on time. Reject rather than invent a refund.
    return {
        "primary_issue": "unsupported_late_claim",
        "case_status": "no_action",
        "cause_code": "DELIVERY_WITHIN_ESTIMATE",
        "responsible_parties": [],
        "recommended_refund_brl": 0.0,
        "resolution_actions": ["reject_late_refund"],
        "confidence": CONF_FALLBACK,
    }


def run(
    task_id: str,
    order_seller: OrderSellerReport,
    delivery: DeliveryReport,
    payment: PaymentReport,
    opened_at: str = "",
) -> A2AMessage:
    decision = _decide(order_seller, delivery, payment, opened_at)
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
