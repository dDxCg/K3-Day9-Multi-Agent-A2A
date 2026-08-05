"""Tools for the Delivery Agent. Scope: delivery timing on orders.csv only."""

from __future__ import annotations

from langchain_core.tools import tool

from src.data_store import get_order, parse_ts


@tool
def lookup_delivery_timing(order_id: str) -> dict:
    """Return the actual customer delivery date, the estimated delivery
    date, and whether delivery to the customer was late (delivered after
    the estimated date) for the given order_id."""
    order = get_order(order_id)
    if order is None:
        return {"found": False, "order_id": order_id}

    delivered = parse_ts(order["order_delivered_customer_date"])
    estimated = parse_ts(order["order_estimated_delivery_date"])
    delivered_late = bool(delivered and estimated and delivered > estimated)

    return {
        "found": True,
        "order_id": order_id,
        "order_delivered_customer_date": order["order_delivered_customer_date"],
        "order_estimated_delivery_date": order["order_estimated_delivery_date"],
        "delivered_late": delivered_late,
    }


DELIVERY_TOOLS = [lookup_delivery_timing]
