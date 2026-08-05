"""EC_POLICY_V1 — bảng quy tắc README mục 4, dạng literal.

Policy Agent đọc bảng từ đây thay vì dựa vào trí nhớ của model: rule engine
chạy first-match-wins theo ĐÚNG thứ tự ưu tiên, LLM không được phép đảo thứ tự
hay chọn "rule khớp nhất".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

PLATFORM_PARTY_ID = "OLIST_PLATFORM"
LOGISTICS_PARTY_ID = "LOGISTICS_PROVIDER"

ROOT_CAUSE_CODES = {
    "SELLER_HANDOFF_AFTER_LIMIT",
    "CARRIER_DELIVERED_AFTER_ESTIMATE",
    "ORDER_CANCELED_AFTER_PAYMENT",
    "ORDER_UNAVAILABLE_AFTER_PAYMENT",
    "MULTIPLE_PAYMENTS_RECONCILED",
    "DELIVERY_WITHIN_ESTIMATE",
}

RECONCILE_TOLERANCE_BRL = 0.10


@dataclass(frozen=True)
class PolicyRule:
    primary_issue: str
    root_cause_code: str
    case_status: str
    action: str
    refund_basis: str  # "payment_total" | "freight_total" | "none"
    party_type: str | None
    party_source: str | None  # "platform" | "late_handoff_sellers" | "logistics"
    base_confidence: float
    condition_text: str
    matches: Callable[[dict[str, Any]], bool]


def _paid(facts: dict[str, Any]) -> bool:
    return float(facts.get("payment_total_brl") or 0.0) > 0.0


def _reconciled(facts: dict[str, Any]) -> bool:
    return bool(facts.get("reconciled"))


# Thứ tự trong list = thứ tự ưu tiên. KHÔNG sắp xếp lại.
POLICY_RULES: list[PolicyRule] = [
    PolicyRule(
        primary_issue="canceled_order_paid",
        root_cause_code="ORDER_CANCELED_AFTER_PAYMENT",
        case_status="action_required",
        action="issue_full_refund",
        refund_basis="payment_total",
        party_type="platform",
        party_source="platform",
        base_confidence=0.95,
        condition_text="order_status = canceled và tổng payment > 0",
        matches=lambda f: f.get("order_status") == "canceled" and _paid(f),
    ),
    PolicyRule(
        primary_issue="unavailable_order_paid",
        root_cause_code="ORDER_UNAVAILABLE_AFTER_PAYMENT",
        case_status="action_required",
        action="issue_full_refund",
        refund_basis="payment_total",
        party_type="platform",
        party_source="platform",
        base_confidence=0.95,
        condition_text="order_status = unavailable và tổng payment > 0",
        matches=lambda f: f.get("order_status") == "unavailable" and _paid(f),
    ),
    PolicyRule(
        primary_issue="late_delivery_seller",
        root_cause_code="SELLER_HANDOFF_AFTER_LIMIT",
        case_status="action_required",
        action="refund_freight",
        refund_basis="freight_total",
        party_type="seller",
        party_source="late_handoff_sellers",
        base_confidence=0.93,
        condition_text="Giao sau estimated date và carrier nhận hàng sau shipping_limit_date",
        matches=lambda f: bool(f.get("late_overall")) and bool(f.get("late_handoff_seller_ids")),
    ),
    PolicyRule(
        primary_issue="late_delivery_logistics",
        root_cause_code="CARRIER_DELIVERED_AFTER_ESTIMATE",
        case_status="action_required",
        action="refund_freight",
        refund_basis="freight_total",
        party_type="logistics_provider",
        party_source="logistics",
        base_confidence=0.92,
        condition_text=(
            "Giao sau estimated date và carrier nhận hàng không muộn hơn shipping_limit_date"
        ),
        matches=lambda f: bool(f.get("late_overall"))
        and not f.get("late_handoff_seller_ids"),
    ),
    PolicyRule(
        primary_issue="valid_split_payment",
        root_cause_code="MULTIPLE_PAYMENTS_RECONCILED",
        case_status="no_action",
        action="explain_valid_split_payment",
        refund_basis="none",
        party_type=None,
        party_source=None,
        base_confidence=0.90,
        condition_text=(
            "Có từ 2 payment row; tổng payment khớp tổng item + freight trong sai số 0.10 BRL"
        ),
        matches=lambda f: int(f.get("payment_row_count") or 0) >= 2 and _reconciled(f),
    ),
    PolicyRule(
        primary_issue="unsupported_late_claim",
        root_cause_code="DELIVERY_WITHIN_ESTIMATE",
        case_status="no_action",
        action="reject_late_refund",
        refund_basis="none",
        party_type=None,
        party_source=None,
        base_confidence=0.88,
        condition_text="Đơn giao không muộn hơn estimated date và payment khớp",
        matches=lambda f: not f.get("late_overall") and _reconciled(f),
    ),
]

# Khi không rule nào khớp (dữ liệu ngoài 6 tình huống chuẩn): chọn kết luận
# no_action + refund 0 với confidence thấp. Đoán bừa một khoản hoàn trên dữ liệu
# không chắc chắn tệ hơn nhiều so với việc thừa nhận không đủ căn cứ.
FALLBACK_RULE = PolicyRule(
    primary_issue="unsupported_late_claim",
    root_cause_code="DELIVERY_WITHIN_ESTIMATE",
    case_status="no_action",
    action="reject_late_refund",
    refund_basis="none",
    party_type=None,
    party_source=None,
    base_confidence=0.35,
    condition_text="Fallback — không rule nào khớp",
    matches=lambda f: True,
)


def evaluate(facts: dict[str, Any]) -> tuple[PolicyRule, bool]:
    """First-match-wins theo đúng thứ tự bảng. Trả (rule, matched_thật_sự)."""
    for rule in POLICY_RULES:
        if rule.matches(facts):
            return rule, True
    return FALLBACK_RULE, False


def refund_for(rule: PolicyRule, facts: dict[str, Any]) -> float:
    if rule.refund_basis == "payment_total":
        return round(float(facts.get("payment_total_brl") or 0.0), 2)
    if rule.refund_basis == "freight_total":
        return round(float(facts.get("freight_total_brl") or 0.0), 2)
    return 0.0


def responsible_parties_for(rule: PolicyRule, facts: dict[str, Any]) -> list[dict[str, str]]:
    if rule.party_source == "platform":
        return [{"party_type": "platform", "party_id": PLATFORM_PARTY_ID}]
    if rule.party_source == "logistics":
        return [{"party_type": "logistics_provider", "party_id": LOGISTICS_PARTY_ID}]
    if rule.party_source == "late_handoff_sellers":
        sellers = list(facts.get("late_handoff_seller_ids") or [])[:3]
        return [{"party_type": "seller", "party_id": sid} for sid in sellers]
    return []


def get_policy_table() -> list[dict[str, str]]:
    """Bảng dạng literal để nhét vào prompt Policy Agent."""
    return [
        {
            "priority": str(idx + 1),
            "primary_issue": rule.primary_issue,
            "condition": rule.condition_text,
            "responsible_party": rule.party_type or "none",
            "refund": rule.refund_basis,
            "action": rule.action,
            "root_cause_code": rule.root_cause_code,
        }
        for idx, rule in enumerate(POLICY_RULES)
    ]
