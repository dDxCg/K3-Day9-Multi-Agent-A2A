"""State dùng chung của graph — cũng chính là blackboard để các agent handoff."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_findings(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Reducer cho nhánh chạy song song: mỗi agent ghi vào key riêng của nó."""
    return {**left, **right}


class CaseState(TypedDict, total=False):
    # --- do Coordinator nạp ---
    case_id: str
    case: dict[str, Any]  # CaseInput đã parse
    order_id: str
    bundle_view: dict[str, Any]  # OrderBundle.to_agent_view()

    # --- do các domain agent ghi (key = tên agent) ---
    findings: Annotated[dict[str, Any], merge_findings]

    # --- do Policy / Verifier ghi ---
    draft: dict[str, Any]  # CaseOutput dạng dict
    verification: dict[str, Any]  # {"ok": bool, "problems": [...]}
    errors: Annotated[list[str], operator.add]
