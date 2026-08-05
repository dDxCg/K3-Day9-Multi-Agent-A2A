from __future__ import annotations

from typing import Any, TypedDict


class CaseState(TypedDict, total=False):
    case_id: str
    order_id: str
    customer_message: str
    policy_version: str
    opened_at: str
    order_seller_report: dict[str, Any]
    delivery_report: dict[str, Any]
    payment_report: dict[str, Any]
    policy_decision: dict[str, Any]
    case_output: dict[str, Any]
