"""Policy Agent — áp EC_POLICY_V1 lên các finding đã handoff, dựng draft output.

Đọc: findings["order_seller"], findings["payment"], findings["delivery"], bundle_view
Ghi: state["draft"] (CaseOutput dạng dict)

Thứ tự ưu tiên quy tắc lấy từ schema.ISSUE_PRIORITY, mapping action/root cause lấy từ
schema.ISSUE_ACTION và schema.ISSUE_ROOT_CAUSE — không hard-code lại ở đây.

Phân loại, refund và evidence do rule engine quyết (tất định, không trôi theo model).
LLM đọc cùng bộ finding và tự chọn primary_issue; kết quả chỉ dùng để hiệu chỉnh
confidence và ghi vào trace, không ghi đè quyết định.
"""

from __future__ import annotations

from typing import Any

from ..llm import call_json, llm_enabled
from ..schema import (
    ISSUE_ACTION,
    ISSUE_PRIORITY,
    ISSUE_ROOT_CAUSE,
    MAX_ENTITY_IDS,
    MAX_EVIDENCE,
    PrimaryIssue,
    ev_item,
    ev_order,
    ev_payment,
    ev_policy,
    ev_seller,
)
from ..state import CaseState
from ..tracing import trace_event

AGENT = "policy"

SYSTEM_PROMPT = """Bạn là Policy Agent áp dụng EC_POLICY_V1.
Chỉ chọn primary_issue trong danh sách cho phép, theo đúng thứ tự ưu tiên.
Refund và action phải suy ra từ quy tắc, không tự chế. Mọi số tiền làm tròn 2 chữ số.

Thứ tự ưu tiên (chọn quy tắc ĐẦU TIÊN thoả điều kiện, dừng ngay):
1. canceled_order_paid      : order_status = canceled và tổng payment > 0
2. unavailable_order_paid   : order_status = unavailable và tổng payment > 0
3. late_delivery_seller     : giao sau ngày dự kiến VÀ có seller bàn giao sau shipping_limit_date
4. late_delivery_logistics  : giao sau ngày dự kiến VÀ không seller nào bàn giao muộn
5. valid_split_payment      : có >= 2 payment row và tổng payment khớp item+freight
6. unsupported_late_claim   : còn lại

Trả đúng một JSON object: {"primary_issue": "<mã>", "notes": "<1 câu tiếng Việt>"}"""

# Bên chịu trách nhiệm cố định theo bảng README mục 4 (seller lấy động từ finding).
FIXED_PARTY: dict[PrimaryIssue, tuple[str, str]] = {
    "canceled_order_paid": ("platform", "OLIST_PLATFORM"),
    "unavailable_order_paid": ("platform", "OLIST_PLATFORM"),
    "late_delivery_logistics": ("logistics_provider", "LOGISTICS_PROVIDER"),
}

BASE_CONFIDENCE = 0.90
AGREE_BONUS = 0.02
DISAGREE_PENALTY = 0.05
CONFIDENCE_RANGE = (0.50, 0.98)


def _classify(state: CaseState) -> PrimaryIssue:
    """Chọn primary_issue theo đúng thứ tự ưu tiên EC_POLICY_V1, dừng ở quy tắc đầu tiên khớp."""
    findings = state.get("findings", {})
    order_seller = findings.get("order_seller", {})
    payment = findings.get("payment", {})
    delivery = findings.get("delivery", {})

    status = order_seller.get("order_status")
    payment_total = float(payment.get("payment_total_brl", 0.0))
    late = bool(delivery.get("late_vs_estimate"))
    late_sellers = order_seller.get("late_handoff_seller_ids") or []

    conditions: dict[PrimaryIssue, bool] = {
        "canceled_order_paid": status == "canceled" and payment_total > 0,
        "unavailable_order_paid": status == "unavailable" and payment_total > 0,
        "late_delivery_seller": late and bool(late_sellers),
        "late_delivery_logistics": late and not late_sellers,
        "valid_split_payment": bool(payment.get("is_split_payment")) and bool(payment.get("reconciled")),
        "unsupported_late_claim": True,  # fallback cuối bảng
    }
    return next(issue for issue in ISSUE_PRIORITY if conditions[issue])


def _refund_brl(issue: PrimaryIssue, view: dict[str, Any]) -> float:
    """Full payment cho canceled/unavailable, freight cho late, 0 cho còn lại."""
    totals = view.get("totals", {})
    if issue in ("canceled_order_paid", "unavailable_order_paid"):
        return round(float(totals.get("payment_total_brl", 0.0)), 2)
    if issue in ("late_delivery_seller", "late_delivery_logistics"):
        return round(float(totals.get("freight_total_brl", 0.0)), 2)
    return 0.0


def _responsible_parties(issue: PrimaryIssue, late_sellers: list[str]) -> list[dict[str, str]]:
    if issue == "late_delivery_seller":
        return [{"party_type": "seller", "party_id": sid} for sid in late_sellers]
    fixed = FIXED_PARTY.get(issue)
    return [{"party_type": fixed[0], "party_id": fixed[1]}] if fixed else []


def _entities(
    issue: PrimaryIssue,
    order_id: str,
    item_ids: list[str],
    seller_ids: list[str],
    late_sellers: list[str],
    payment_ids: list[str],
) -> dict[str, list[str]]:
    # Seller vi phạm được ưu tiên đưa lên đầu để không bị cắt khi quá 5 ID.
    ordered_sellers = late_sellers + [s for s in seller_ids if s not in late_sellers]
    return {
        "order_ids": [order_id],
        "item_ids": item_ids[:MAX_ENTITY_IDS],
        "seller_ids": ordered_sellers[:MAX_ENTITY_IDS],
        "payment_ids": payment_ids[:MAX_ENTITY_IDS],
    }


def _evidence(
    issue: PrimaryIssue,
    root_cause: str,
    order_id: str,
    item_ids: list[str],
    late_sellers: list[str],
    seller_ids: list[str],
    payment_ids: list[str],
) -> list[str]:
    """Xếp evidence theo mức liên quan tới quy tắc đang áp, cắt còn tối đa 10 ID.

    ID dựng từ chính dữ liệu đã join nên không thể là false positive.
    """
    def _items(limit: int) -> list[str]:
        return [ev_item(*i.split(":", 1)) for i in item_ids[:limit]]

    def _payments(limit: int) -> list[str]:
        return [ev_payment(*p.split(":", 1)) for p in payment_ids[:limit]]

    head = [ev_order(order_id), ev_policy(root_cause)]

    if issue in ("canceled_order_paid", "unavailable_order_paid"):
        # Trọng tâm: đã trả tiền cho đơn không giao được.
        body = _payments(4) + _items(3) + [ev_seller(s) for s in seller_ids[:1]]
    elif issue == "late_delivery_seller":
        body = [ev_seller(s) for s in late_sellers[:2]] + _items(4) + _payments(2)
    elif issue == "late_delivery_logistics":
        body = _items(4) + _payments(2) + [ev_seller(s) for s in seller_ids[:2]]
    elif issue == "valid_split_payment":
        body = _payments(5) + _items(2) + [ev_seller(s) for s in seller_ids[:1]]
    else:  # unsupported_late_claim
        body = _items(3) + _payments(3) + [ev_seller(s) for s in seller_ids[:2]]

    seen: list[str] = []
    for ev in head + body:
        if ev not in seen:
            seen.append(ev)
    return seen[:MAX_EVIDENCE]


def _confidence(state: CaseState, llm_issue: str | None, issue: PrimaryIssue) -> float:
    """Chốt tất định ở BASE_CONFIDENCE, cộng/trừ theo mức đồng thuận của các agent.

    Đây là điểm đồng thuận (agreement score) giữa rule engine và LLM, KHÔNG phải
    xác suất calibrated. Vote thiếu (None, LLM bị skip) không cộng/trừ điểm.
    """
    findings = state.get("findings", {})
    votes = [findings.get(name, {}).get("llm_agrees") for name in ("order_seller", "payment", "delivery")]
    if llm_issue is not None:
        votes.append(llm_issue == issue)

    score = BASE_CONFIDENCE
    for vote in votes:
        if vote is True:
            score += AGREE_BONUS
        elif vote is False:
            score -= DISAGREE_PENALTY
    low, high = CONFIDENCE_RANGE
    return round(min(high, max(low, score)), 2)


def policy_node(state: CaseState) -> dict[str, Any]:
    view = state["bundle_view"]
    totals = view.get("totals", {})
    findings = state.get("findings", {})
    order_seller = findings.get("order_seller", {})
    payment = findings.get("payment", {})

    issue = _classify(state)
    root_cause = ISSUE_ROOT_CAUSE[issue]
    action = ISSUE_ACTION[issue]
    refund = _refund_brl(issue, view)

    item_ids = order_seller.get("item_ids") or []
    seller_ids = order_seller.get("seller_ids") or []
    late_sellers = order_seller.get("late_handoff_seller_ids") or []
    payment_ids = payment.get("payment_ids") or []

    llm_issue = _llm_second_opinion(state, issue)
    confidence = _confidence(state, llm_issue, issue)

    draft = {
        "case_id": state["case_id"],
        "assessment": {
            "primary_issue": issue,
            "case_status": "action_required" if refund > 0 else "no_action",
            "confidence": confidence,
        },
        "affected_entities": _entities(
            issue, state["order_id"], item_ids, seller_ids, late_sellers, payment_ids
        ),
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": root_cause, "rank": 1}],
            "responsible_parties": _responsible_parties(issue, late_sellers),
        },
        "evidence_ids": _evidence(
            issue, root_cause, state["order_id"], item_ids, late_sellers, seller_ids, payment_ids
        ),
        "financial_resolution": {
            "currency": "BRL",
            "item_total_brl": round(float(totals.get("item_total_brl", 0.0)), 2),
            "freight_total_brl": round(float(totals.get("freight_total_brl", 0.0)), 2),
            "payment_total_brl": round(float(totals.get("payment_total_brl", 0.0)), 2),
            "recommended_refund_brl": refund,
        },
        "resolution_actions": [action],
    }

    trace_event(
        case_id=state["case_id"],
        agent=AGENT,
        event="decision",
        payload={
            "primary_issue": issue,
            "llm_primary_issue": llm_issue,
            "confidence": confidence,
            "refund_brl": refund,
            "action": action,
            "root_cause": root_cause,
            "n_evidence": len(draft["evidence_ids"]),
        },
    )
    return {"draft": draft}


def _llm_second_opinion(state: CaseState, rule_issue: PrimaryIssue) -> str | None:
    """Model tự phân loại từ cùng bộ finding. Chỉ dùng để đo đồng thuận, không ghi đè."""
    if not llm_enabled():
        return None
    findings = state.get("findings", {})
    order_seller = findings.get("order_seller", {})
    payment = findings.get("payment", {})
    delivery = findings.get("delivery", {})
    user = (
        "Kết quả điều tra từ các agent chuyên trách:\n"
        f"- order_status: {order_seller.get('order_status')}\n"
        f"- seller bàn giao muộn: {order_seller.get('late_handoff_seller_ids')}\n"
        f"- giao sau ngày dự kiến: {delivery.get('late_vs_estimate')}\n"
        f"- số payment row: {payment.get('n_payment_rows')}\n"
        f"- tổng payment khớp item+freight: {payment.get('reconciled')}\n"
        f"- tổng payment: {payment.get('payment_total_brl')} BRL\n\n"
        "Áp EC_POLICY_V1 và cho biết primary_issue."
    )
    try:
        reply = call_json(agent=AGENT, case_id=state["case_id"], system=SYSTEM_PROMPT, user=user)
    except Exception as err:  # noqa: BLE001
        trace_event(
            case_id=state["case_id"],
            agent=AGENT,
            event="llm_skipped",
            payload={"error": f"{type(err).__name__}: {err}", "rule_issue": rule_issue},
        )
        return None
    claimed = reply.get("primary_issue")
    return claimed if isinstance(claimed, str) else None
