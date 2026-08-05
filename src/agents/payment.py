"""Payment Agent — đối soát payment với item + freight.

Đọc: bundle_view.payments, bundle_view.totals
Ghi vào findings["payment"]:
    {
      "payment_total_brl": float,
      "expected_total_brl": float,      # item_total + freight_total
      "delta_brl": float,
      "reconciled": bool,               # |delta| <= PAYMENT_TOLERANCE_BRL
      "n_payment_rows": int,
      "is_split_payment": bool,         # >= 2 payment row
      "payment_ids": ["<order_id>:<payment_sequential>"],
      "notes": str
    }
Lưu ý README: payment_value là tiền của từng payment row, KHÔNG phải từng installment.

Số học là tất định (code); LLM chỉ đọc lại con số và xác nhận kết luận đối soát.
"""

from __future__ import annotations

from typing import Any

from ..llm import call_json, llm_enabled
from ..schema import PAYMENT_TOLERANCE_BRL
from ..state import CaseState
from ..tracing import trace_event

AGENT = "payment"

SYSTEM_PROMPT = f"""Bạn là Payment Agent. Đối soát tổng payment với tổng item + freight.
Chỉ dùng số liệu được cung cấp, làm tròn 2 chữ số thập phân, không tự tạo giao dịch.
Coi là khớp khi chênh lệch tuyệt đối <= {PAYMENT_TOLERANCE_BRL} BRL.
Trả đúng một JSON object: {{"reconciled": bool, "notes": "<1 câu tiếng Việt>"}}"""


def payment_node(state: CaseState) -> dict[str, Any]:
    view = state["bundle_view"]
    order_id = state["order_id"]
    payments = view.get("payments", [])
    totals = view.get("totals", {})

    payment_total = round(float(totals.get("payment_total_brl", 0.0)), 2)
    expected_total = round(
        float(totals.get("item_total_brl", 0.0)) + float(totals.get("freight_total_brl", 0.0)), 2
    )
    delta = round(payment_total - expected_total, 2)
    reconciled = abs(delta) <= PAYMENT_TOLERANCE_BRL

    payment_ids = [f"{order_id}:{p['payment_sequential']}" for p in payments]

    if not payments:
        notes = "Đơn không có payment row."
    elif reconciled:
        notes = (
            f"{len(payments)} payment row, tổng {payment_total:.2f} BRL khớp "
            f"item+freight {expected_total:.2f} BRL (lệch {delta:.2f})."
        )
    else:
        notes = (
            f"{len(payments)} payment row, tổng {payment_total:.2f} BRL lệch {delta:.2f} BRL "
            f"so với item+freight {expected_total:.2f} BRL."
        )

    finding: dict[str, Any] = {
        "payment_total_brl": payment_total,
        "expected_total_brl": expected_total,
        "delta_brl": delta,
        "reconciled": reconciled,
        "n_payment_rows": len(payments),
        "is_split_payment": len(payments) >= 2,
        "payment_ids": payment_ids,
        "notes": notes,
    }

    finding["llm_agrees"] = _llm_review(state, finding, payments)
    trace_event(case_id=state["case_id"], agent=AGENT, event="finding", payload=finding)
    return {"findings": {AGENT: finding}}


def _llm_review(
    state: CaseState, finding: dict[str, Any], payments: list[dict[str, Any]]
) -> bool | None:
    if not llm_enabled():
        return None
    lines = "\n".join(
        f"- payment_sequential={p['payment_sequential']} type={p['payment_type']} "
        f"installments={p['payment_installments']} payment_value={p['payment_value']}"
        for p in payments
    ) or "- (không có payment row)"
    user = (
        f"Các payment row của đơn:\n{lines}\n\n"
        f"Tổng payment: {finding['payment_total_brl']:.2f} BRL\n"
        f"Tổng item + freight: {finding['expected_total_brl']:.2f} BRL\n\n"
        "Tổng payment có khớp tổng item + freight không?"
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
    return bool(reply.get("reconciled")) == finding["reconciled"]
