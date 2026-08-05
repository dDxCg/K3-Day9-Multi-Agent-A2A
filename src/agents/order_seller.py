"""Order & Seller Agent — trạng thái đơn, danh sách item, seller, mốc bàn giao.

Đọc: bundle_view.order_status, .items, .timestamps.order_delivered_carrier_date
Ghi vào findings["order_seller"]:
    {
      "order_status": str,
      "has_items": bool,
      "seller_ids": [str],
      "late_handoff_seller_ids": [str],   # carrier_date > shipping_limit_date của item seller đó
      "item_ids": ["<order_id>:<order_item_id>"],
      "notes": str
    }

Quy ước nhiều item (README mục 4): seller bị coi là bàn giao muộn nếu
order_delivered_carrier_date > shipping_limit_date của bất kỳ item nào thuộc seller đó.
Phần so sánh là tất định (code); LLM chỉ xác nhận lại và diễn giải.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..llm import call_json, llm_enabled
from ..state import CaseState
from ..tracing import trace_event

AGENT = "order_seller"

SYSTEM_PROMPT = """Bạn là Order & Seller Agent trong hệ thống xử lý khiếu nại e-commerce.
Chỉ dùng dữ liệu được cung cấp. Tuyệt đối không bịa order, item, seller hay mốc thời gian.
Một seller bàn giao muộn khi order_delivered_carrier_date muộn hơn shipping_limit_date
của item thuộc seller đó.
Trả đúng một JSON object: {"late_handoff_seller_ids": [<seller_id>], "notes": "<1 câu tiếng Việt>"}"""


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def order_seller_node(state: CaseState) -> dict[str, Any]:
    view = state["bundle_view"]
    order_id = state["order_id"]
    items = view.get("items", [])
    carrier_dt = _parse(view.get("timestamps", {}).get("order_delivered_carrier_date"))

    seller_ids: list[str] = []
    item_ids: list[str] = []
    late_sellers: list[str] = []

    for item in items:
        seller_id = item["seller_id"]
        if seller_id not in seller_ids:
            seller_ids.append(seller_id)
        item_ids.append(f"{order_id}:{item['order_item_id']}")

        limit_dt = _parse(item.get("shipping_limit_date"))
        if carrier_dt and limit_dt and carrier_dt > limit_dt and seller_id not in late_sellers:
            late_sellers.append(seller_id)

    if not items:
        notes = f"Đơn không có item row (order_status={view.get('order_status')})."
    elif late_sellers:
        notes = f"{len(late_sellers)}/{len(seller_ids)} seller bàn giao sau shipping_limit_date."
    elif carrier_dt is None:
        notes = "Chưa có mốc bàn giao cho carrier nên không xét seller trễ hạn."
    else:
        notes = f"Toàn bộ {len(seller_ids)} seller bàn giao trong hạn."

    finding: dict[str, Any] = {
        "order_status": view.get("order_status"),
        "has_items": bool(items),
        "seller_ids": seller_ids,
        "late_handoff_seller_ids": late_sellers,
        "item_ids": item_ids,
        "notes": notes,
    }

    finding["llm_agrees"] = _llm_review(state, finding, items, view)
    trace_event(case_id=state["case_id"], agent=AGENT, event="finding", payload=finding)
    return {"findings": {AGENT: finding}}


def _llm_review(
    state: CaseState, finding: dict[str, Any], items: list[dict[str, Any]], view: dict[str, Any]
) -> bool | None:
    if not llm_enabled() or not items:
        return None
    lines = "\n".join(
        f"- order_item_id={i['order_item_id']} seller_id={i['seller_id']} "
        f"shipping_limit_date={i['shipping_limit_date']}"
        for i in items
    )
    user = (
        f"order_delivered_carrier_date: {view.get('timestamps', {}).get('order_delivered_carrier_date')}\n"
        f"Các item trong đơn:\n{lines}\n\n"
        "Seller nào bàn giao muộn?"
    )
    try:
        reply = call_json(agent=AGENT, case_id=state["case_id"], system=SYSTEM_PROMPT, user=user)
    except Exception as err:  # noqa: BLE001
        trace_event(
            case_id=state["case_id"],
            agent=AGENT,
            event="llm_skipped",
            payload={"error": f"{type(err).__name__}: {err}"},
        )
        return None
    if isinstance(reply.get("notes"), str) and reply["notes"].strip():
        finding["llm_notes"] = reply["notes"].strip()
    claimed = reply.get("late_handoff_seller_ids")
    if not isinstance(claimed, list):
        return False
    return sorted(str(s) for s in claimed) == sorted(finding["late_handoff_seller_ids"])
