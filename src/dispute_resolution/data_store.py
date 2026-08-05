from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .config import DEFAULT_POLICY
from .domain import CaseRequest, ItemRecord, OrderRecord, PaymentRecord


def parse_datetime(value: str | None) -> datetime | None:
    value = (value or "").strip()
    return datetime.fromisoformat(value) if value else None


def parse_decimal(value: str | None) -> Decimal:
    value = (value or "").strip()
    return Decimal(value) if value else Decimal("0")


def numeric_key(value: str) -> tuple[int, str]:
    try:
        return int(value), value
    except ValueError:
        return 0, value


class DataStore:
    """Read-only, case-scoped Olist data store."""

    def __init__(self, data_dir: Path, cases: tuple[CaseRequest, ...]) -> None:
        self.data_dir = data_dir
        self.case_order_ids = {case.order_id for case in cases}
        self.orders: dict[str, OrderRecord] = {}
        self.items_by_order: dict[str, list[ItemRecord]] = defaultdict(list)
        self.payments_by_order: dict[str, list[PaymentRecord]] = defaultdict(list)
        self.seller_ids: set[str] = set()
        self._load()

    @staticmethod
    def load_cases(input_dir: Path) -> tuple[CaseRequest, ...]:
        cases: list[CaseRequest] = []
        for path in sorted(input_dir.glob("EC_*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            request = payload["customer_request"]
            case = CaseRequest(
                case_id=payload["case_id"],
                opened_at=payload["opened_at"],
                language=request["language"],
                message=request["message"],
                order_id=request["claimed_order_id"],
                policy_version=payload["policy_version"],
            )
            if case.case_id != path.stem:
                raise ValueError(f"Case ID mismatch in {path}")
            if case.policy_version != DEFAULT_POLICY:
                raise ValueError(
                    f"Unsupported policy {case.policy_version!r} in {path.name}"
                )
            cases.append(case)
        if len(cases) != 50:
            raise ValueError(f"Expected 50 input cases, found {len(cases)}")
        if len({case.case_id for case in cases}) != len(cases):
            raise ValueError("Duplicate case_id detected")
        return tuple(cases)

    def _load(self) -> None:
        self._load_orders()
        self._load_items()
        self._load_payments()
        self._load_sellers()
        missing_orders = self.case_order_ids - self.orders.keys()
        if missing_orders:
            raise ValueError(f"Orders not found: {sorted(missing_orders)}")

    def _load_orders(self) -> None:
        path = self.data_dir / "olist_orders_dataset.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                order_id = row["order_id"]
                if order_id not in self.case_order_ids:
                    continue
                self.orders[order_id] = OrderRecord(
                    order_id=order_id,
                    customer_id=row["customer_id"],
                    status=row["order_status"],
                    purchased_at=parse_datetime(row["order_purchase_timestamp"]),
                    approved_at=parse_datetime(row["order_approved_at"]),
                    delivered_carrier_at=parse_datetime(
                        row["order_delivered_carrier_date"]
                    ),
                    delivered_customer_at=parse_datetime(
                        row["order_delivered_customer_date"]
                    ),
                    estimated_delivery_at=parse_datetime(
                        row["order_estimated_delivery_date"]
                    ),
                )

    def _load_items(self) -> None:
        path = self.data_dir / "olist_order_items_dataset.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                order_id = row["order_id"]
                if order_id not in self.case_order_ids:
                    continue
                self.items_by_order[order_id].append(
                    ItemRecord(
                        order_id=order_id,
                        item_id=row["order_item_id"],
                        product_id=row["product_id"],
                        seller_id=row["seller_id"],
                        shipping_limit_at=parse_datetime(row["shipping_limit_date"]),
                        price=parse_decimal(row["price"]),
                        freight=parse_decimal(row["freight_value"]),
                    )
                )
        for items in self.items_by_order.values():
            items.sort(key=lambda item: numeric_key(item.item_id))

    def _load_payments(self) -> None:
        path = self.data_dir / "olist_order_payments_dataset.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                order_id = row["order_id"]
                if order_id not in self.case_order_ids:
                    continue
                self.payments_by_order[order_id].append(
                    PaymentRecord(
                        order_id=order_id,
                        sequential=row["payment_sequential"],
                        payment_type=row["payment_type"],
                        installments=int(row["payment_installments"] or 0),
                        value=parse_decimal(row["payment_value"]),
                    )
                )
        for payments in self.payments_by_order.values():
            payments.sort(key=lambda payment: numeric_key(payment.sequential))

    def _load_sellers(self) -> None:
        path = self.data_dir / "olist_sellers_dataset.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            self.seller_ids = {row["seller_id"] for row in csv.DictReader(handle)}

    def get_order(self, order_id: str) -> OrderRecord:
        try:
            return self.orders[order_id]
        except KeyError as exc:
            raise KeyError(f"Order not found: {order_id}") from exc

    def get_items(self, order_id: str) -> tuple[ItemRecord, ...]:
        return tuple(self.items_by_order.get(order_id, ()))

    def get_payments(self, order_id: str) -> tuple[PaymentRecord, ...]:
        return tuple(self.payments_by_order.get(order_id, ()))
