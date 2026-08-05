from __future__ import annotations

from decimal import Decimal

from .data_store import DataStore
from .domain import (
    PAYMENT_TOLERANCE,
    CaseRequest,
    DeliveryHandoff,
    OrderSellerHandoff,
    PaymentHandoff,
    PolicyDecision,
    money,
)
from .policy import apply_policy


def unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


class OrderSellerAgent:
    name = "OrderSellerAgent"

    def __init__(self, store: DataStore) -> None:
        self.store = store

    def analyze(self, case: CaseRequest) -> OrderSellerHandoff:
        order = self.store.get_order(case.order_id)
        items = self.store.get_items(case.order_id)
        item_total = money(sum((item.price for item in items), Decimal("0")))
        freight_total = money(sum((item.freight for item in items), Decimal("0")))
        seller_ids = unique([item.seller_id for item in items])

        late_sellers: list[str] = []
        if order.delivered_carrier_at:
            for item in items:
                if (
                    item.shipping_limit_at
                    and order.delivered_carrier_at > item.shipping_limit_at
                ):
                    late_sellers.append(item.seller_id)

        return OrderSellerHandoff(
            order=order,
            items=items,
            item_total=item_total,
            freight_total=freight_total,
            seller_ids=seller_ids,
            late_seller_ids=unique(late_sellers),
        )


class PaymentAgent:
    name = "PaymentAgent"

    def __init__(self, store: DataStore) -> None:
        self.store = store

    def analyze(
        self, case: CaseRequest, order_facts: OrderSellerHandoff
    ) -> PaymentHandoff:
        payments = self.store.get_payments(case.order_id)
        payment_total = money(sum((payment.value for payment in payments), Decimal("0")))
        expected_total = money(order_facts.item_total + order_facts.freight_total)
        reconciled = abs(payment_total - expected_total) <= PAYMENT_TOLERANCE
        return PaymentHandoff(
            payments=payments,
            payment_total=payment_total,
            expected_total=expected_total,
            reconciled=reconciled,
            split_payment=len(payments) >= 2,
        )


class DeliveryAgent:
    name = "DeliveryAgent"

    def analyze(self, order_facts: OrderSellerHandoff) -> DeliveryHandoff:
        order = order_facts.order
        delivered_late = bool(
            order.delivered_customer_at
            and order.estimated_delivery_at
            and order.delivered_customer_at > order.estimated_delivery_at
        )
        return DeliveryHandoff(
            delivered_customer_at=order.delivered_customer_at,
            estimated_delivery_at=order.estimated_delivery_at,
            delivered_late=delivered_late,
            seller_handoff_late=bool(order_facts.late_seller_ids),
        )


class PolicyAgent:
    name = "PolicyAgent"

    def analyze(
        self,
        order_facts: OrderSellerHandoff,
        payment_facts: PaymentHandoff,
        delivery_facts: DeliveryHandoff,
    ) -> PolicyDecision:
        return apply_policy(order_facts, payment_facts, delivery_facts)
