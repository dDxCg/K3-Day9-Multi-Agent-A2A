"""CaseFile — object handoff chảy xuyên suốt pipeline.

Mỗi agent chỉ append đúng phần của mình; Coordinator đọc toàn bộ để dựng
output cuối. Đây chính là "A2A message payload" được log vào trace.jsonl.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class OrderSellerFindings:
    """Kết quả Order & Seller Agent."""

    order_found: bool = False
    order_status: str | None = None
    order_delivered_carrier_date: str | None = None
    order_delivered_customer_date: str | None = None
    order_estimated_delivery_date: str | None = None
    item_count: int = 0
    item_total_brl: float = 0.0
    freight_total_brl: float = 0.0
    seller_ids: list[str] = field(default_factory=list)
    # Seller bàn giao muộn: carrier_date > shipping_limit_date của item thuộc seller đó
    late_handoff_seller_ids: list[str] = field(default_factory=list)
    item_entity_ids: list[str] = field(default_factory=list)  # "<order_id>:<n>"
    evidence_ids: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class DeliveryFindings:
    """Kết quả Delivery Agent."""

    delivered: bool = False
    late_overall: bool = False  # giao sau estimated date
    delivered_customer_date: str | None = None
    estimated_delivery_date: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class PaymentFindings:
    """Kết quả Payment Agent."""

    payment_row_count: int = 0
    payment_total_brl: float = 0.0
    reconciled: bool = False  # khớp item+freight trong sai số 0.10 BRL
    delta_brl: float = 0.0
    payment_entity_ids: list[str] = field(default_factory=list)  # "<order_id>:<seq>"
    evidence_ids: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class PolicyDecision:
    """Kết quả Policy Agent."""

    primary_issue: str
    root_cause_code: str
    case_status: str
    recommended_refund_brl: float
    resolution_actions: list[str]
    responsible_parties: list[dict[str, str]]
    confidence: float = 0.5
    llm_flagged: bool = False
    notes: str = ""


@dataclass
class Verification:
    """Kết quả Verifier Agent.

    errors = lỗi cứng, phải sửa trước khi ghi file.
    warnings = tín hiệu đáng ngờ (vd kết luận lệch với ý định khiếu nại) — chỉ
    hạ confidence, KHÔNG chặn output, vì dữ liệu luôn thắng lời khiếu nại.
    """

    passed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    attempt: int = 1
    notes: str = ""


@dataclass
class CaseFile:
    """Hồ sơ case, mỗi agent append đúng phần của mình."""

    case_id: str
    claimed_order_id: str
    opened_at: str | None = None
    policy_version: str | None = None
    customer_message: str = ""

    order_seller: OrderSellerFindings | None = None
    delivery: DeliveryFindings | None = None
    payment: PaymentFindings | None = None
    policy: PolicyDecision | None = None
    verification: Verification | None = None

    final_output: dict[str, Any] | None = None

    def facts_for_policy(self) -> dict[str, Any]:
        """Gom facts từ 3 agent domain để Policy Agent áp bảng quy tắc."""
        os_f = self.order_seller or OrderSellerFindings()
        dl_f = self.delivery or DeliveryFindings()
        pm_f = self.payment or PaymentFindings()
        return {
            "order_found": os_f.order_found,
            "order_status": os_f.order_status,
            "item_total_brl": os_f.item_total_brl,
            "freight_total_brl": os_f.freight_total_brl,
            "late_handoff_seller_ids": list(os_f.late_handoff_seller_ids),
            "seller_ids": list(os_f.seller_ids),
            "late_overall": dl_f.late_overall,
            "delivered": dl_f.delivered,
            "payment_total_brl": pm_f.payment_total_brl,
            "payment_row_count": pm_f.payment_row_count,
            "reconciled": pm_f.reconciled,
        }

    def to_trace_dict(self) -> dict[str, Any]:
        return asdict(self)
