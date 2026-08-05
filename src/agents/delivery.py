"""Delivery Agent — so thời điểm giao thực tế với hạn giao dự kiến.

Đọc: bundle_view.timestamps (order_delivered_customer_date, order_estimated_delivery_date,
order_delivered_carrier_date)
Ghi vào findings["delivery"]:
    {
      "delivered": bool,
      "delivered_at": str | None,
      "estimated_at": str | None,
      "carrier_handoff_at": str | None,
      "late_vs_estimate": bool,     # delivered_customer_date > estimated_delivery_date
      "days_late": float | None,
      "notes": str
    }
So sánh timestamp theo đúng giá trị trong CSV, không đổi múi giờ.

Phần tính toán là tất định (code). LLM chỉ đọc lại cùng dữ liệu để xác nhận độc lập
và diễn giải bằng lời — kết luận số học không phụ thuộc model.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..llm import call_json, llm_enabled
from ..state import CaseState
from ..tracing import trace_event

AGENT = "delivery"

SYSTEM_PROMPT = """Bạn là Delivery Agent. So sánh mốc giao thực tế với hạn giao dự kiến.
Nếu thiếu mốc thời gian thì báo thiếu, không suy đoán ngày giao.
Trả đúng một JSON object: {"late_vs_estimate": bool, "notes": "<1 câu tiếng Việt>"}"""


def _parse(value: Any) -> datetime | None:
    """Timestamp trong bundle_view là chuỗi 'YYYY-MM-DD HH:MM:SS'."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def delivery_node(state: CaseState) -> dict[str, Any]:
    view = state["bundle_view"]
    ts = view.get("timestamps", {})

    delivered_at = ts.get("order_delivered_customer_date")
    estimated_at = ts.get("order_estimated_delivery_date")
    carrier_at = ts.get("order_delivered_carrier_date")

    delivered_dt = _parse(delivered_at)
    estimated_dt = _parse(estimated_at)

    late = bool(delivered_dt and estimated_dt and delivered_dt > estimated_dt)
    days_late = (
        round((delivered_dt - estimated_dt).total_seconds() / 86400, 2)
        if delivered_dt and estimated_dt
        else None
    )

    if delivered_dt is None:
        notes = f"Chưa có mốc giao cho khách (order_status={view.get('order_status')})."
    elif late:
        notes = f"Giao trễ {days_late} ngày so với hạn dự kiến."
    else:
        notes = f"Giao đúng hạn, sớm hơn dự kiến {abs(days_late or 0)} ngày."

    finding: dict[str, Any] = {
        "delivered": delivered_dt is not None,
        "delivered_at": delivered_at,
        "estimated_at": estimated_at,
        "carrier_handoff_at": carrier_at,
        "late_vs_estimate": late,
        "days_late": days_late,
        "notes": notes,
    }

    finding["llm_agrees"] = _llm_review(state, finding)
    trace_event(case_id=state["case_id"], agent=AGENT, event="finding", payload=finding)
    return {"findings": {AGENT: finding}}


def _llm_review(state: CaseState, finding: dict[str, Any]) -> bool | None:
    """Cho model đọc lại cùng mốc thời gian và tự kết luận. None = không gọi LLM."""
    if not llm_enabled():
        return None
    user = (
        "Mốc thời gian của đơn (giá trị thô trong CSV, không đổi múi giờ):\n"
        f"- order_delivered_customer_date: {finding['delivered_at']}\n"
        f"- order_estimated_delivery_date: {finding['estimated_at']}\n"
        f"- order_delivered_carrier_date: {finding['carrier_handoff_at']}\n\n"
        "Đơn có bị giao sau ngày dự kiến không?"
    )
    try:
        reply = call_json(agent=AGENT, case_id=state["case_id"], system=SYSTEM_PROMPT, user=user)
    except Exception as err:  # noqa: BLE001 — LLM chỉ là lớp xác nhận, không chặn pipeline
        trace_event(
            case_id=state["case_id"],
            agent=AGENT,
            event="llm_skipped",
            payload={"error": f"{type(err).__name__}: {err}"},
        )
        return None
    if isinstance(reply.get("notes"), str) and reply["notes"].strip():
        finding["llm_notes"] = reply["notes"].strip()
    return bool(reply.get("late_vs_estimate")) == finding["late_vs_estimate"]
