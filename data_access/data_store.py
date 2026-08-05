"""Tầng dữ liệu deterministic — nguồn sự thật duy nhất của hệ thống.

Agent LLM không tự tính tiền, không tự gõ evidence ID: mọi con số và ID đều
lấy từ đây. Load 9 CSV một lần rồi index theo order_id / seller_id.

Quy ước so sánh thời gian: so trực tiếp giá trị timestamp trong CSV, không đổi
múi giờ (README mục 2). estimated_delivery_date luôn ở mốc 00:00:00 nên "giao
sau estimated date" được hiểu là delivered_customer_date > estimated_delivery_date
theo đúng giá trị thô.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from data_access import evidence as ev

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _parse_ts(raw: object) -> datetime | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"nan", "nat"}:
        return None
    try:
        return datetime.strptime(text, _TS_FORMAT)
    except ValueError:
        return None


def _clean_str(raw: object) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    text = str(raw).strip()
    return text or None


@dataclass(frozen=True)
class OrderRecord:
    order_id: str
    customer_id: str | None
    order_status: str
    purchase_timestamp: str | None
    approved_at: str | None
    delivered_carrier_date: str | None
    delivered_customer_date: str | None
    estimated_delivery_date: str | None
    evidence_id: str

    @property
    def delivered_carrier_dt(self) -> datetime | None:
        return _parse_ts(self.delivered_carrier_date)

    @property
    def delivered_customer_dt(self) -> datetime | None:
        return _parse_ts(self.delivered_customer_date)

    @property
    def estimated_delivery_dt(self) -> datetime | None:
        return _parse_ts(self.estimated_delivery_date)


@dataclass(frozen=True)
class ItemRecord:
    order_id: str
    order_item_id: int
    product_id: str | None
    seller_id: str | None
    shipping_limit_date: str | None
    price: float
    freight_value: float
    evidence_id: str
    entity_id: str

    @property
    def shipping_limit_dt(self) -> datetime | None:
        return _parse_ts(self.shipping_limit_date)


@dataclass(frozen=True)
class PaymentRecord:
    order_id: str
    payment_sequential: int
    payment_type: str | None
    payment_installments: int
    payment_value: float
    evidence_id: str
    entity_id: str


@dataclass(frozen=True)
class SellerRecord:
    seller_id: str
    seller_city: str | None
    seller_state: str | None
    evidence_id: str


class DataStore:
    """Read-only access tới 9 CSV Olist. Không agent nào được ghi vào data/."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self._orders: dict[str, OrderRecord] = {}
        self._items: dict[str, list[ItemRecord]] = {}
        self._payments: dict[str, list[PaymentRecord]] = {}
        self._sellers: dict[str, SellerRecord] = {}
        self._load()

    # ------------------------------------------------------------------ load

    def _load(self) -> None:
        self._load_orders()
        self._load_items()
        self._load_payments()
        self._load_sellers()

    def _load_orders(self) -> None:
        df = pd.read_csv(self.data_dir / "olist_orders_dataset.csv", dtype=str)
        for row in df.itertuples(index=False):
            order_id = str(row.order_id).strip()
            self._orders[order_id] = OrderRecord(
                order_id=order_id,
                customer_id=_clean_str(row.customer_id),
                order_status=(_clean_str(row.order_status) or "").lower(),
                purchase_timestamp=_clean_str(row.order_purchase_timestamp),
                approved_at=_clean_str(row.order_approved_at),
                delivered_carrier_date=_clean_str(row.order_delivered_carrier_date),
                delivered_customer_date=_clean_str(row.order_delivered_customer_date),
                estimated_delivery_date=_clean_str(row.order_estimated_delivery_date),
                evidence_id=ev.order_evidence(order_id),
            )

    def _load_items(self) -> None:
        df = pd.read_csv(self.data_dir / "olist_order_items_dataset.csv")
        for row in df.itertuples(index=False):
            order_id = str(row.order_id).strip()
            item_id = int(row.order_item_id)
            record = ItemRecord(
                order_id=order_id,
                order_item_id=item_id,
                product_id=_clean_str(row.product_id),
                seller_id=_clean_str(row.seller_id),
                shipping_limit_date=_clean_str(row.shipping_limit_date),
                price=float(row.price),
                freight_value=float(row.freight_value),
                evidence_id=ev.item_evidence(order_id, item_id),
                entity_id=ev.item_entity_id(order_id, item_id),
            )
            self._items.setdefault(order_id, []).append(record)
        for rows in self._items.values():
            rows.sort(key=lambda r: r.order_item_id)

    def _load_payments(self) -> None:
        df = pd.read_csv(self.data_dir / "olist_order_payments_dataset.csv")
        for row in df.itertuples(index=False):
            order_id = str(row.order_id).strip()
            seq = int(row.payment_sequential)
            record = PaymentRecord(
                order_id=order_id,
                payment_sequential=seq,
                payment_type=_clean_str(row.payment_type),
                payment_installments=int(row.payment_installments),
                payment_value=float(row.payment_value),
                evidence_id=ev.payment_evidence(order_id, seq),
                entity_id=ev.payment_entity_id(order_id, seq),
            )
            self._payments.setdefault(order_id, []).append(record)
        for rows in self._payments.values():
            rows.sort(key=lambda r: r.payment_sequential)

    def _load_sellers(self) -> None:
        df = pd.read_csv(self.data_dir / "olist_sellers_dataset.csv", dtype=str)
        for row in df.itertuples(index=False):
            seller_id = str(row.seller_id).strip()
            self._sellers[seller_id] = SellerRecord(
                seller_id=seller_id,
                seller_city=_clean_str(row.seller_city),
                seller_state=_clean_str(row.seller_state),
                evidence_id=ev.seller_evidence(seller_id),
            )

    # ------------------------------------------------------------------ tools

    def get_order(self, order_id: str) -> OrderRecord | None:
        return self._orders.get(order_id)

    def get_items(self, order_id: str) -> list[ItemRecord]:
        return list(self._items.get(order_id, []))

    def get_payments(self, order_id: str) -> list[PaymentRecord]:
        return list(self._payments.get(order_id, []))

    def get_seller(self, seller_id: str) -> SellerRecord | None:
        return self._sellers.get(seller_id)

    # -------------------------------------------------------------- verifier

    def evidence_exists(self, evidence_id: str) -> bool:
        """Verifier dùng: ID có resolve về một dòng CSV thật không."""
        if not ev.is_well_formed(evidence_id):
            return False
        parts = evidence_id.split(":")
        kind = parts[0]

        if kind == "order":
            return parts[1] in self._orders
        if kind == "seller":
            return parts[1] in self._sellers
        if kind == "item":
            return any(i.order_item_id == int(parts[2]) for i in self._items.get(parts[1], []))
        if kind == "payment":
            return any(
                p.payment_sequential == int(parts[2]) for p in self._payments.get(parts[1], [])
            )
        if kind == "policy":
            from data_access.policy_table import ROOT_CAUSE_CODES

            return parts[1] in ROOT_CAUSE_CODES
        return False

    def totals(self, order_id: str) -> tuple[float, float, float]:
        """(item_total, freight_total, payment_total) — làm tròn 2 chữ số."""
        items = self.get_items(order_id)
        payments = self.get_payments(order_id)
        item_total = round(sum(i.price for i in items), 2)
        freight_total = round(sum(i.freight_value for i in items), 2)
        payment_total = round(sum(p.payment_value for p in payments), 2)
        return item_total, freight_total, payment_total
