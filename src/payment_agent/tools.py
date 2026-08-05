"""Tools for the Payment Agent. Scope: order_payments.csv, reconciled
against the item/freight totals handed off by the Order & Seller Agent.
"""

from __future__ import annotations

from langchain_core.tools import tool

from src.data_store import get_payments

RECONCILE_TOLERANCE_BRL = 0.10


@tool
def lookup_payments(order_id: str) -> dict:
    """Return every payment row for an order_id and their total value,
    rounded to 2 decimals."""
    payments = get_payments(order_id)
    total = round(sum(float(p["payment_value"]) for p in payments), 2)
    return {
        "order_id": order_id,
        "payments": payments,
        "payment_count": len(payments),
        "payment_total_brl": total,
    }


@tool
def reconcile_with_order_total(
    order_id: str, item_total_brl: float, freight_total_brl: float
) -> dict:
    """Check whether this order's total payments match item_total_brl +
    freight_total_brl within a 0.10 BRL tolerance (the 'valid split
    payment' condition requires >=2 payment rows AND a match)."""
    payments = get_payments(order_id)
    payment_total = round(sum(float(p["payment_value"]) for p in payments), 2)
    expected = round(float(item_total_brl) + float(freight_total_brl), 2)
    matches = abs(payment_total - expected) <= RECONCILE_TOLERANCE_BRL
    return {
        "order_id": order_id,
        "payment_total_brl": payment_total,
        "expected_total_brl": expected,
        "payment_count": len(payments),
        "matches_item_freight": matches,
        "is_valid_split_payment": matches and len(payments) >= 2,
    }


PAYMENT_TOOLS = [lookup_payments, reconcile_with_order_total]
