"""Single source of truth for reading the Olist CSVs.

Loaded once per process and shared by every agent's tools, so each agent
only sees the columns relevant to its own domain function (see the
`get_*` accessors below) even though the underlying files are the same.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_TIMESTAMP_COLUMNS = {
    "olist_orders_dataset.csv": [
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ],
    "olist_order_items_dataset.csv": ["shipping_limit_date"],
}


@lru_cache(maxsize=None)
def _load(csv_name: str) -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / csv_name)
    for col in _TIMESTAMP_COLUMNS.get(csv_name, []):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def _row_to_dict(row: pd.Series) -> dict[str, Any]:
    out = {}
    for key, value in row.items():
        if isinstance(value, pd.Timestamp):
            out[key] = None if pd.isna(value) else value.isoformat()
        elif pd.isna(value):
            out[key] = None
        else:
            out[key] = value
    return out


def get_order(order_id: str) -> dict[str, Any] | None:
    df = _load("olist_orders_dataset.csv")
    match = df[df["order_id"] == order_id]
    if match.empty:
        return None
    return _row_to_dict(match.iloc[0])


def get_order_items(order_id: str) -> list[dict[str, Any]]:
    df = _load("olist_order_items_dataset.csv")
    match = df[df["order_id"] == order_id]
    return [_row_to_dict(row) for _, row in match.iterrows()]


def get_payments(order_id: str) -> list[dict[str, Any]]:
    df = _load("olist_order_payments_dataset.csv")
    match = df[df["order_id"] == order_id]
    return [_row_to_dict(row) for _, row in match.iterrows()]


def get_seller(seller_id: str) -> dict[str, Any] | None:
    df = _load("olist_sellers_dataset.csv")
    match = df[df["seller_id"] == seller_id]
    if match.empty:
        return None
    return _row_to_dict(match.iloc[0])


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)
