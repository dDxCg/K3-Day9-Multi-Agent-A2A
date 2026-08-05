"""Tools for the Order & Seller agent.

Scope: orders, order_items, sellers CSVs only. Every number returned here
is computed in Python (not by the LLM) so the agent only has to copy
values into its structured report.
"""

from __future__ import annotations

from langchain_core.tools import tool

from src.data_store import get_order, get_order_items, get_seller, parse_ts


@tool
def lookup_order(order_id: str) -> dict:
    """Look up an order's status and delivery/handoff timestamps by order_id."""
    order = get_order(order_id)
    if order is None:
        return {"found": False, "order_id": order_id}
    return {
        "found": True,
        "order_id": order["order_id"],
        "order_status": order["order_status"],
        "order_delivered_carrier_date": order["order_delivered_carrier_date"],
        "order_delivered_customer_date": order["order_delivered_customer_date"],
        "order_estimated_delivery_date": order["order_estimated_delivery_date"],
    }


@tool
def lookup_order_items_and_sellers(order_id: str) -> dict:
    """Return every item row for an order (item id, seller, price, freight,
    shipping_limit_date), the seller(s) that handed off to the carrier after
    their shipping_limit_date, and item/freight totals rounded to 2 decimals.
    """
    items = get_order_items(order_id)
    order = get_order(order_id)
    carrier_date = parse_ts(order["order_delivered_carrier_date"]) if order else None

    item_rows = []
    item_total = 0.0
    freight_total = 0.0
    seller_ids: list[str] = []
    late_seller_ids: list[str] = []

    for item in items:
        seller_id = item["seller_id"]
        item_total += float(item["price"])
        freight_total += float(item["freight_value"])
        if seller_id not in seller_ids:
            seller_ids.append(seller_id)

        shipping_limit = parse_ts(item["shipping_limit_date"])
        is_late = bool(carrier_date and shipping_limit and carrier_date > shipping_limit)
        if is_late and seller_id not in late_seller_ids:
            late_seller_ids.append(seller_id)

        item_rows.append(
            {
                "order_item_id": item["order_item_id"],
                "seller_id": seller_id,
                "price": item["price"],
                "freight_value": item["freight_value"],
                "shipping_limit_date": item["shipping_limit_date"],
                "seller_handed_off_late": is_late,
            }
        )

    return {
        "order_id": order_id,
        "items": item_rows,
        "seller_ids": seller_ids,
        "late_seller_ids": late_seller_ids,
        "item_total_brl": round(item_total, 2),
        "freight_total_brl": round(freight_total, 2),
    }


@tool
def lookup_seller(seller_id: str) -> dict:
    """Look up a seller's record by seller_id, to confirm it exists in the data."""
    seller = get_seller(seller_id)
    if seller is None:
        return {"found": False, "seller_id": seller_id}
    return {"found": True, **seller}


ORDER_SELLER_TOOLS = [lookup_order, lookup_order_items_and_sellers, lookup_seller]
