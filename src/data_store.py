"""Truy cập dữ liệu Olist.

Chỉ nạp CSV cần cho bài toán. `geolocation`, `products`, `reviews`, `sellers`
nạp lazy vì không nằm trên đường chính của quy tắc nghiệp vụ.

Timestamp parse naive (không đổi múi giờ) đúng như README mục 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any

import pandas as pd

from .config import DATA_DIR

_TS_COLS_ORDERS = [
    "order_purchase_timestamp",
    "order_approved_at",
    "order_delivered_carrier_date",
    "order_delivered_customer_date",
    "order_estimated_delivery_date",
]


def _read(name: str, **kwargs: Any) -> pd.DataFrame:
    path = DATA_DIR / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Thiếu {path}. Tải dataset Olist vào data/.")
    return pd.read_csv(path, **kwargs)


@lru_cache(maxsize=1)
def orders() -> pd.DataFrame:
    df = _read("olist_orders_dataset", parse_dates=_TS_COLS_ORDERS)
    return df.set_index("order_id", drop=False)


@lru_cache(maxsize=1)
def order_items() -> pd.DataFrame:
    df = _read("olist_order_items_dataset", parse_dates=["shipping_limit_date"])
    return df.sort_values(["order_id", "order_item_id"])


@lru_cache(maxsize=1)
def order_payments() -> pd.DataFrame:
    df = _read("olist_order_payments_dataset")
    return df.sort_values(["order_id", "payment_sequential"])


@lru_cache(maxsize=1)
def customers() -> pd.DataFrame:
    return _read("olist_customers_dataset").set_index("customer_id", drop=False)


@lru_cache(maxsize=1)
def sellers() -> pd.DataFrame:
    return _read("olist_sellers_dataset").set_index("seller_id", drop=False)


@lru_cache(maxsize=1)
def _items_by_order() -> dict[str, pd.DataFrame]:
    return {oid: g for oid, g in order_items().groupby("order_id", sort=False)}


@lru_cache(maxsize=1)
def _payments_by_order() -> dict[str, pd.DataFrame]:
    return {oid: g for oid, g in order_payments().groupby("order_id", sort=False)}


def _dt(value: Any) -> datetime | None:
    """Trả None cho NaT/NaN để agent so sánh an toàn."""
    if value is None or pd.isna(value):
        return None
    return value.to_pydatetime() if isinstance(value, pd.Timestamp) else value


@dataclass
class OrderBundle:
    """Toàn bộ dữ liệu thô của một order, dùng chung cho mọi agent."""

    order_id: str
    found: bool
    order: dict[str, Any]
    items: list[dict[str, Any]]
    payments: list[dict[str, Any]]
    customer: dict[str, Any]

    # --- tổng tiền, làm tròn 2 chữ số ------------------------------------
    @property
    def item_total_brl(self) -> float:
        return round(sum(float(i["price"]) for i in self.items), 2)

    @property
    def freight_total_brl(self) -> float:
        return round(sum(float(i["freight_value"]) for i in self.items), 2)

    @property
    def payment_total_brl(self) -> float:
        return round(sum(float(p["payment_value"]) for p in self.payments), 2)

    @property
    def seller_ids(self) -> list[str]:
        seen: list[str] = []
        for item in self.items:
            if item["seller_id"] not in seen:
                seen.append(item["seller_id"])
        return seen

    def to_agent_view(self) -> dict[str, Any]:
        """Bản rút gọn, JSON-serializable, đưa vào prompt agent."""
        return {
            "order_id": self.order_id,
            "found": self.found,
            "order_status": self.order.get("order_status"),
            "timestamps": {
                k: (v.isoformat(sep=" ") if isinstance(v, datetime) else None)
                for k, v in self.order.items()
                if k.startswith("order_") and isinstance(v, (datetime, type(None)))
            },
            "items": [
                {
                    "order_item_id": int(i["order_item_id"]),
                    "seller_id": i["seller_id"],
                    "shipping_limit_date": (
                        i["shipping_limit_date"].isoformat(sep=" ")
                        if isinstance(i["shipping_limit_date"], datetime)
                        else None
                    ),
                    "price": float(i["price"]),
                    "freight_value": float(i["freight_value"]),
                }
                for i in self.items
            ],
            "payments": [
                {
                    "payment_sequential": int(p["payment_sequential"]),
                    "payment_type": p["payment_type"],
                    "payment_installments": int(p["payment_installments"]),
                    "payment_value": float(p["payment_value"]),
                }
                for p in self.payments
            ],
            "totals": {
                "item_total_brl": self.item_total_brl,
                "freight_total_brl": self.freight_total_brl,
                "payment_total_brl": self.payment_total_brl,
            },
        }


def load_order_bundle(order_id: str) -> OrderBundle:
    odf = orders()
    if order_id not in odf.index:
        return OrderBundle(order_id, False, {}, [], [], {})

    row = odf.loc[order_id]
    order = {k: _dt(v) if k in _TS_COLS_ORDERS else v for k, v in row.to_dict().items()}

    items_df = _items_by_order().get(order_id)
    items = []
    if items_df is not None:
        for rec in items_df.to_dict("records"):
            rec["shipping_limit_date"] = _dt(rec["shipping_limit_date"])
            items.append(rec)

    pay_df = _payments_by_order().get(order_id)
    payments = pay_df.to_dict("records") if pay_df is not None else []

    cdf = customers()
    cid = order.get("customer_id")
    customer = cdf.loc[cid].to_dict() if cid in cdf.index else {}

    return OrderBundle(order_id, True, order, items, payments, customer)
