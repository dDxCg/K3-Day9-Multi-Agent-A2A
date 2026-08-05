from __future__ import annotations

from decimal import Decimal

from .domain import (
    DeliveryHandoff,
    OrderSellerHandoff,
    PaymentHandoff,
    PolicyDecision,
    money,
)


ALLOWED_ISSUES = {
    "canceled_order_paid",
    "unavailable_order_paid",
    "late_delivery_seller",
    "late_delivery_logistics",
    "valid_split_payment",
    "unsupported_late_claim",
}

ALLOWED_CAUSES = {
    "SELLER_HANDOFF_AFTER_LIMIT",
    "CARRIER_DELIVERED_AFTER_ESTIMATE",
    "ORDER_CANCELED_AFTER_PAYMENT",
    "ORDER_UNAVAILABLE_AFTER_PAYMENT",
    "MULTIPLE_PAYMENTS_RECONCILED",
    "DELIVERY_WITHIN_ESTIMATE",
}

# EC_POLICY_V1 is applied to complete, directly verified dataset facts. The
# confidence is deterministic and intentionally leaves a small uncertainty
# margin for source-data quality rather than policy-rule ambiguity.
DECISION_CONFIDENCE = 0.99


class UnclassifiedCaseError(ValueError):
    pass


def apply_policy(
    order_facts: OrderSellerHandoff,
    payment_facts: PaymentHandoff,
    delivery_facts: DeliveryHandoff,
) -> PolicyDecision:
    """Apply EC_POLICY_V1 in README priority order."""
    status = order_facts.order.status

    if status == "canceled" and payment_facts.payment_total > 0:
        return PolicyDecision(
            primary_issue="canceled_order_paid",
            cause_code="ORDER_CANCELED_AFTER_PAYMENT",
            responsible_parties=(("platform", "OLIST_PLATFORM"),),
            refund=money(payment_facts.payment_total),
            action="issue_full_refund",
            confidence=DECISION_CONFIDENCE,
        )

    if status == "unavailable" and payment_facts.payment_total > 0:
        return PolicyDecision(
            primary_issue="unavailable_order_paid",
            cause_code="ORDER_UNAVAILABLE_AFTER_PAYMENT",
            responsible_parties=(("platform", "OLIST_PLATFORM"),),
            refund=money(payment_facts.payment_total),
            action="issue_full_refund",
            confidence=DECISION_CONFIDENCE,
        )

    if delivery_facts.delivered_late and delivery_facts.seller_handoff_late:
        parties = tuple(("seller", seller_id) for seller_id in order_facts.late_seller_ids)
        return PolicyDecision(
            primary_issue="late_delivery_seller",
            cause_code="SELLER_HANDOFF_AFTER_LIMIT",
            responsible_parties=parties,
            refund=money(order_facts.freight_total),
            action="refund_freight",
            confidence=DECISION_CONFIDENCE,
        )

    if delivery_facts.delivered_late and not delivery_facts.seller_handoff_late:
        return PolicyDecision(
            primary_issue="late_delivery_logistics",
            cause_code="CARRIER_DELIVERED_AFTER_ESTIMATE",
            responsible_parties=(("logistics_provider", "LOGISTICS_PROVIDER"),),
            refund=money(order_facts.freight_total),
            action="refund_freight",
            confidence=DECISION_CONFIDENCE,
        )

    if payment_facts.split_payment and payment_facts.reconciled:
        return PolicyDecision(
            primary_issue="valid_split_payment",
            cause_code="MULTIPLE_PAYMENTS_RECONCILED",
            responsible_parties=(),
            refund=Decimal("0.00"),
            action="explain_valid_split_payment",
            confidence=DECISION_CONFIDENCE,
        )

    if not delivery_facts.delivered_late and payment_facts.reconciled:
        return PolicyDecision(
            primary_issue="unsupported_late_claim",
            cause_code="DELIVERY_WITHIN_ESTIMATE",
            responsible_parties=(),
            refund=Decimal("0.00"),
            action="reject_late_refund",
            confidence=DECISION_CONFIDENCE,
        )

    raise UnclassifiedCaseError(
        "Case does not match EC_POLICY_V1: "
        f"order={order_facts.order.order_id}, status={status}, "
        f"delivered_late={delivery_facts.delivered_late}, "
        f"payment_reconciled={payment_facts.reconciled}"
    )
