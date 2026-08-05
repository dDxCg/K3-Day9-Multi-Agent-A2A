from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


CENT = Decimal("0.01")
PAYMENT_TOLERANCE = Decimal("0.10")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def money_float(value: Decimal) -> float:
    return float(money(value))


def iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ") if value else None


@dataclass(frozen=True, slots=True)
class CaseRequest:
    case_id: str
    opened_at: str
    language: str
    message: str
    order_id: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class OrderRecord:
    order_id: str
    customer_id: str
    status: str
    purchased_at: datetime | None
    approved_at: datetime | None
    delivered_carrier_at: datetime | None
    delivered_customer_at: datetime | None
    estimated_delivery_at: datetime | None


@dataclass(frozen=True, slots=True)
class ItemRecord:
    order_id: str
    item_id: str
    product_id: str
    seller_id: str
    shipping_limit_at: datetime | None
    price: Decimal
    freight: Decimal


@dataclass(frozen=True, slots=True)
class PaymentRecord:
    order_id: str
    sequential: str
    payment_type: str
    installments: int
    value: Decimal


@dataclass(frozen=True, slots=True)
class OrderSellerHandoff:
    order: OrderRecord
    items: tuple[ItemRecord, ...]
    item_total: Decimal
    freight_total: Decimal
    seller_ids: tuple[str, ...]
    late_seller_ids: tuple[str, ...]

    def trace_payload(self) -> dict[str, Any]:
        return {
            "order_id": self.order.order_id,
            "order_status": self.order.status,
            "carrier_handoff_at": iso_or_none(self.order.delivered_carrier_at),
            "item_ids": [item.item_id for item in self.items],
            "seller_ids": list(self.seller_ids),
            "late_seller_ids": list(self.late_seller_ids),
            "shipping_limits": {
                item.item_id: iso_or_none(item.shipping_limit_at) for item in self.items
            },
            "item_total_brl": money_float(self.item_total),
            "freight_total_brl": money_float(self.freight_total),
        }


@dataclass(frozen=True, slots=True)
class PaymentHandoff:
    payments: tuple[PaymentRecord, ...]
    payment_total: Decimal
    expected_total: Decimal
    reconciled: bool
    split_payment: bool

    def trace_payload(self) -> dict[str, Any]:
        return {
            "payment_ids": [payment.sequential for payment in self.payments],
            "payment_row_count": len(self.payments),
            "payment_total_brl": money_float(self.payment_total),
            "expected_total_brl": money_float(self.expected_total),
            "difference_brl": money_float(abs(self.payment_total - self.expected_total)),
            "reconciled_within_0_10_brl": self.reconciled,
            "split_payment": self.split_payment,
        }


@dataclass(frozen=True, slots=True)
class DeliveryHandoff:
    delivered_customer_at: datetime | None
    estimated_delivery_at: datetime | None
    delivered_late: bool
    seller_handoff_late: bool

    def trace_payload(self) -> dict[str, Any]:
        return {
            "delivered_customer_at": iso_or_none(self.delivered_customer_at),
            "estimated_delivery_at": iso_or_none(self.estimated_delivery_at),
            "delivered_late": self.delivered_late,
            "seller_handoff_late": self.seller_handoff_late,
        }


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    primary_issue: str
    cause_code: str
    responsible_parties: tuple[tuple[str, str], ...]
    refund: Decimal
    action: str
    confidence: float

    @property
    def case_status(self) -> str:
        return "action_required" if self.refund > 0 else "no_action"

    def trace_payload(self) -> dict[str, Any]:
        return {
            "primary_issue": self.primary_issue,
            "cause_code": self.cause_code,
            "responsible_parties": [
                {"party_type": party_type, "party_id": party_id}
                for party_type, party_id in self.responsible_parties
            ],
            "recommended_refund_brl": money_float(self.refund),
            "action": self.action,
            "confidence": self.confidence,
        }
