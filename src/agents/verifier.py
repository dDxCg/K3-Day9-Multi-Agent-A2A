"""Verifier Agent — chốt chặn trước khi ghi file.

Kiểm: schema pydantic, giới hạn số lượng ID, định dạng evidence, evidence có thật
trong CSV, tính nhất quán refund/action/case_status. Không sửa nội dung nghiệp vụ,
chỉ báo problem để Coordinator quyết định.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from ..schema import (
    ISSUE_ACTION,
    ISSUE_ROOT_CAUSE,
    MAX_ACTIONS,
    MAX_ENTITY_IDS,
    MAX_EVIDENCE,
    MAX_RESPONSIBLE_PARTIES,
    MAX_ROOT_CAUSES,
    CaseOutput,
)
from ..state import CaseState
from ..tracing import trace_event

AGENT = "verifier"

_EVIDENCE_PATTERNS = (
    re.compile(r"^order:[0-9a-f]{32}$"),
    re.compile(r"^item:[0-9a-f]{32}:\d+$"),
    re.compile(r"^payment:[0-9a-f]{32}:\d+$"),
    re.compile(r"^seller:[0-9a-f]{32}$"),
    re.compile(r"^policy:[A-Z_]+$"),
)


def _known_evidence(view: dict[str, Any], order_id: str) -> set[str]:
    """Tập evidence dựng được trực tiếp từ dữ liệu của order này."""
    ids = {f"order:{order_id}"}
    for item in view.get("items", []):
        ids.add(f"item:{order_id}:{item['order_item_id']}")
        ids.add(f"seller:{item['seller_id']}")
    for pay in view.get("payments", []):
        ids.add(f"payment:{order_id}:{pay['payment_sequential']}")
    for code in ISSUE_ROOT_CAUSE.values():
        ids.add(f"policy:{code}")
    return ids


def verifier_node(state: CaseState) -> dict[str, Any]:
    draft = state.get("draft") or {}
    view = state["bundle_view"]
    order_id = state["order_id"]
    problems: list[str] = []

    try:
        parsed = CaseOutput.model_validate(draft)
    except ValidationError as err:
        problems.append(f"schema_invalid: {err.error_count()} lỗi")
        trace_event(
            case_id=state["case_id"],
            agent=AGENT,
            event="verdict",
            payload={"ok": False, "problems": problems, "detail": err.errors()[:3]},
        )
        return {"verification": {"ok": False, "problems": problems}}

    if parsed.case_id != state["case_id"]:
        problems.append("case_id_mismatch")

    ent = parsed.affected_entities
    for name, values in (
        ("order_ids", ent.order_ids),
        ("item_ids", ent.item_ids),
        ("seller_ids", ent.seller_ids),
        ("payment_ids", ent.payment_ids),
    ):
        if len(values) > MAX_ENTITY_IDS:
            problems.append(f"{name}_over_limit")
    if len(parsed.evidence_ids) > MAX_EVIDENCE:
        problems.append("evidence_over_limit")
    if len(parsed.root_cause_analysis.ranked_causes) > MAX_ROOT_CAUSES:
        problems.append("root_causes_over_limit")
    if len(parsed.root_cause_analysis.responsible_parties) > MAX_RESPONSIBLE_PARTIES:
        problems.append("responsible_parties_over_limit")
    if len(parsed.resolution_actions) > MAX_ACTIONS:
        problems.append("actions_over_limit")

    known = _known_evidence(view, order_id)
    for ev in parsed.evidence_ids:
        if not any(p.match(ev) for p in _EVIDENCE_PATTERNS):
            problems.append(f"evidence_malformed:{ev}")
        elif ev not in known:
            problems.append(f"evidence_not_in_data:{ev}")

    issue = parsed.assessment.primary_issue
    expected_action = ISSUE_ACTION[issue]
    if expected_action not in parsed.resolution_actions:
        problems.append(f"action_mismatch:expected {expected_action}")

    fin = parsed.financial_resolution
    refund = fin.recommended_refund_brl
    if refund < 0:
        problems.append("refund_negative")
    if refund > fin.payment_total_brl + 0.01:
        problems.append("refund_exceeds_payment")
    expected_status = "action_required" if refund > 0 else "no_action"
    if parsed.assessment.case_status != expected_status:
        problems.append(f"case_status_mismatch:expected {expected_status}")

    for value in (fin.item_total_brl, fin.freight_total_brl, fin.payment_total_brl, refund):
        if round(value, 2) != value:
            problems.append("amount_not_rounded_2dp")
            break

    ok = not problems
    trace_event(
        case_id=state["case_id"],
        agent=AGENT,
        event="verdict",
        payload={"ok": ok, "problems": problems},
    )
    return {"verification": {"ok": ok, "problems": problems}}
