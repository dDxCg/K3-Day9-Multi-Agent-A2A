"""Verifier Agent — chốt chặn trước khi ghi file.

Kiểm tra ĐỘC LẬP: mọi con số được tính lại từ CSV chứ không đọc lại findings
của agent khác, để không có agent nào tự chấm bài của chính mình.
"""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from data_access import evidence as ev
from data_access import policy_table
from llm import prompts
from schema.case_file import CaseFile, Verification
from schema.output_schema import (
    MAX_ACTIONS,
    MAX_ENTITY_IDS,
    MAX_EVIDENCE,
    MAX_RESPONSIBLE_PARTIES,
    MAX_ROOT_CAUSES,
)

_MONEY_EPS = 0.011  # sai số cho phép của phép làm tròn 2 chữ số

# Từ khoá nhận diện ý định trong lời khiếu nại. Kiểm theo thứ tự từ cụ thể đến
# chung: "nhiều dòng thanh toán" cũng chứa chữ "thanh toán" của nhóm PAID_INCOMPLETE.
_INTENT_KEYWORDS = [
    ("SPLIT", ("thu trùng", "nhiều dòng thanh toán", "đối soát")),
    ("LATE", ("trễ", "chậm", "muộn")),
    ("PAID_INCOMPLETE", ("không được hoàn tất", "chưa hoàn tất", "đã thanh toán")),
]


def detect_intent(message: str) -> str | None:
    """Đoán nhóm vấn đề khách hàng đang phản ánh. None nếu không nhận ra."""
    lowered = (message or "").lower()
    for family, keywords in _INTENT_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return family
    return None


class VerifierAgent(BaseAgent):
    name = "verifier_agent"

    def run(self, case_file: CaseFile, draft: dict[str, Any], attempt: int = 1) -> Verification:
        errors: list[str] = []
        order_id = case_file.claimed_order_id

        errors += self._check_evidence(draft)
        errors += self._check_entities(draft, order_id)
        errors += self._check_financials(draft, order_id)
        errors += self._check_limits(draft)
        errors += self._check_consistency(draft)
        warnings = self._check_claim_intent(case_file, draft)

        verification = Verification(
            passed=not errors, errors=errors, warnings=warnings, attempt=attempt
        )

        reply = self.ask(
            prompts.VERIFIER_SYSTEM,
            prompts.verifier_user(
                case_file.case_id,
                {"passed": verification.passed, "errors": errors[:5], "warnings": warnings[:3]},
            ),
            max_tokens=64,
        )
        if reply.get("note"):
            verification.notes = str(reply["note"])[:300]

        self.emit(
            case_file.case_id,
            "coordinator",
            "verification",
            {
                "passed": verification.passed,
                "errors": errors,
                "warnings": warnings,
                "attempt": attempt,
            },
        )
        return verification

    # ------------------------------------------------------------- các phép check

    @staticmethod
    def _check_claim_intent(case_file: CaseFile, draft: dict[str, Any]) -> list[str]:
        """Đối chiếu nhóm kết luận với nhóm vấn đề khách hàng phản ánh.

        Đây là WARNING chứ không phải error: dữ liệu CSV luôn thắng lời khiếu
        nại (README mục 1). Ví dụ hợp lệ và phổ biến: khách kêu giao trễ nhưng
        dữ liệu cho thấy giao đúng hạn → unsupported_late_claim, vẫn cùng nhóm
        LATE nên không có cảnh báo. Cảnh báo chỉ nổ khi lệch hẳn NHÓM, dấu hiệu
        của lỗi hệ thống (vd ai đó đảo thứ tự bảng rule).
        """
        intent = detect_intent(case_file.customer_message)
        if intent is None:
            return []
        issue = draft.get("assessment", {}).get("primary_issue")
        family = policy_table.ISSUE_FAMILY.get(issue)
        if family and family != intent:
            return [f"kết luận nhóm {family} nhưng khiếu nại thuộc nhóm {intent}"]
        return []

    def _check_evidence(self, draft: dict[str, Any]) -> list[str]:
        errors = []
        for evidence_id in draft.get("evidence_ids", []):
            if not ev.is_well_formed(evidence_id):
                errors.append(f"evidence sai định dạng: {evidence_id}")
            elif not self.store.evidence_exists(evidence_id):
                errors.append(f"evidence không tồn tại trong CSV: {evidence_id}")
        return errors

    def _check_entities(self, draft: dict[str, Any], order_id: str) -> list[str]:
        errors = []
        entities = draft.get("affected_entities", {})

        for oid in entities.get("order_ids", []):
            if self.store.get_order(oid) is None:
                errors.append(f"order_id không tồn tại: {oid}")

        real_items = {i.entity_id for i in self.store.get_items(order_id)}
        for iid in entities.get("item_ids", []):
            if iid not in real_items:
                errors.append(f"item_id không tồn tại: {iid}")

        real_payments = {p.entity_id for p in self.store.get_payments(order_id)}
        for pid in entities.get("payment_ids", []):
            if pid not in real_payments:
                errors.append(f"payment_id không tồn tại: {pid}")

        for sid in entities.get("seller_ids", []):
            if self.store.get_seller(sid) is None:
                errors.append(f"seller_id không tồn tại: {sid}")
        return errors

    def _check_financials(self, draft: dict[str, Any], order_id: str) -> list[str]:
        errors = []
        financial = draft.get("financial_resolution", {})
        item_total, freight_total, payment_total = self.store.totals(order_id)

        expected = {
            "item_total_brl": item_total,
            "freight_total_brl": freight_total,
            "payment_total_brl": payment_total,
        }
        for field, want in expected.items():
            got = float(financial.get(field, 0.0))
            if abs(got - want) > _MONEY_EPS:
                errors.append(f"{field}={got} nhưng CSV cho {want}")

        primary_issue = draft.get("assessment", {}).get("primary_issue")
        rule = next((r for r in policy_table.POLICY_RULES if r.primary_issue == primary_issue), None)
        if rule is None:
            errors.append(f"primary_issue không hợp lệ: {primary_issue}")
        else:
            want_refund = policy_table.refund_for(
                rule,
                {"payment_total_brl": payment_total, "freight_total_brl": freight_total},
            )
            got_refund = float(financial.get("recommended_refund_brl", 0.0))
            if abs(got_refund - want_refund) > _MONEY_EPS:
                errors.append(
                    f"refund={got_refund} không khớp công thức {rule.refund_basis}={want_refund}"
                )
        return errors

    @staticmethod
    def _check_limits(draft: dict[str, Any]) -> list[str]:
        errors = []
        entities = draft.get("affected_entities", {})
        for key, values in entities.items():
            if len(values) > MAX_ENTITY_IDS:
                errors.append(f"{key} có {len(values)} phần tử (>{MAX_ENTITY_IDS})")

        if len(draft.get("evidence_ids", [])) > MAX_EVIDENCE:
            errors.append(f"evidence_ids vượt {MAX_EVIDENCE}")

        rca = draft.get("root_cause_analysis", {})
        if len(rca.get("ranked_causes", [])) > MAX_ROOT_CAUSES:
            errors.append(f"ranked_causes vượt {MAX_ROOT_CAUSES}")
        if len(rca.get("responsible_parties", [])) > MAX_RESPONSIBLE_PARTIES:
            errors.append(f"responsible_parties vượt {MAX_RESPONSIBLE_PARTIES}")
        if len(draft.get("resolution_actions", [])) > MAX_ACTIONS:
            errors.append(f"resolution_actions vượt {MAX_ACTIONS}")

        confidence = draft.get("assessment", {}).get("confidence")
        if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
            errors.append(f"confidence ngoài [0,1]: {confidence}")
        return errors

    @staticmethod
    def _check_consistency(draft: dict[str, Any]) -> list[str]:
        errors = []
        assessment = draft.get("assessment", {})
        status = assessment.get("case_status")
        refund = float(draft.get("financial_resolution", {}).get("recommended_refund_brl", 0.0))

        if status not in {"action_required", "no_action"}:
            errors.append(f"case_status không hợp lệ: {status}")
        elif status == "action_required" and refund <= 0:
            errors.append("case_status=action_required nhưng refund=0")
        elif status == "no_action" and refund > 0:
            errors.append("case_status=no_action nhưng refund>0")

        for cause in draft.get("root_cause_analysis", {}).get("ranked_causes", []):
            if cause.get("cause_code") not in policy_table.ROOT_CAUSE_CODES:
                errors.append(f"cause_code không hợp lệ: {cause.get('cause_code')}")
        return errors
