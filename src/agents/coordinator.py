"""Coordinator Agent — nạp case, phát dữ liệu cho các domain agent, đóng gói kết quả.

Quyền truy cập: input/*.json, data/* (qua data_store). Không tự suy luận nghiệp vụ.
"""

from __future__ import annotations

from typing import Any

from ..data_store import load_order_bundle
from ..schema import CaseInput
from ..state import CaseState
from ..tracing import trace_event


def coordinator_node(state: CaseState) -> dict[str, Any]:
    case = CaseInput.model_validate(state["case"])
    order_id = case.customer_request.claimed_order_id
    bundle = load_order_bundle(order_id)

    trace_event(
        case_id=case.case_id,
        agent="coordinator",
        event="dispatch",
        payload={
            "order_id": order_id,
            "order_found": bundle.found,
            "n_items": len(bundle.items),
            "n_payments": len(bundle.payments),
            "targets": ["order_seller", "payment", "delivery"],
        },
    )

    return {
        "case_id": case.case_id,
        "order_id": order_id,
        "bundle_view": bundle.to_agent_view(),
        "findings": {},
        "errors": [],
    }


def finalize_node(state: CaseState) -> dict[str, Any]:
    """Chốt output sau verifier. Nếu verify fail thì giữ nguyên draft và ghi lỗi."""
    verification = state.get("verification", {})
    trace_event(
        case_id=state["case_id"],
        agent="coordinator",
        event="finalize",
        payload={
            "verified": verification.get("ok"),
            "problems": verification.get("problems", []),
            "primary_issue": (state.get("draft") or {}).get("assessment", {}).get("primary_issue"),
        },
    )
    return {}
