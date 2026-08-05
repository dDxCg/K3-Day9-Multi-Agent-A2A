"""Policy Agent — áp EC_POLICY_V1.

Rule engine chạy first-match-wins theo ĐÚNG thứ tự bảng README mục 4. LLM đóng
vai người soát lần hai: chỉ được RAISE CỜ mâu thuẫn, không được đổi kết luận và
không được nâng confidence.

Vì sao chặt như vậy: bộ 50 case map sạch vào đúng một rule mỗi case, nên mọi
"đóng góp sáng tạo" của model 8B ở đây chỉ có thể làm giảm precision của
root_cause_analysis (15% điểm) chứ không thể tăng.
"""

from __future__ import annotations

from agents.base import BaseAgent
from data_access import policy_table
from llm import prompts
from schema.case_file import CaseFile, PolicyDecision


class PolicyAgent(BaseAgent):
    name = "policy_agent"

    def run(self, case_file: CaseFile, retry_errors: list[str] | None = None) -> PolicyDecision:
        facts = case_file.facts_for_policy()
        rule, matched = policy_table.evaluate(facts)

        refund = policy_table.refund_for(rule, facts)
        parties = policy_table.responsible_parties_for(rule, facts)

        confidence = rule.base_confidence
        notes: list[str] = []
        if not matched:
            notes.append("Không rule nào khớp — dùng fallback no_action.")
        if not facts.get("order_found"):
            confidence = min(confidence, 0.20)
            notes.append("Không tìm thấy order trong CSV.")
        if rule.party_source == "late_handoff_sellers" and not parties:
            confidence = min(confidence, 0.50)
            notes.append("Rule chỉ seller chịu trách nhiệm nhưng không xác định được seller.")
        if retry_errors:
            confidence = round(confidence * 0.9, 2)
            notes.append(f"Chạy lại sau lỗi verify: {'; '.join(retry_errors[:3])}")

        decision = PolicyDecision(
            primary_issue=rule.primary_issue,
            root_cause_code=rule.root_cause_code,
            case_status=rule.case_status,
            recommended_refund_brl=refund,
            resolution_actions=[rule.action],
            responsible_parties=parties,
            confidence=confidence,
            notes=" ".join(notes),
        )

        reply = self.ask(
            prompts.POLICY_SYSTEM,
            prompts.policy_user(
                case_file.case_id,
                facts,
                rule.primary_issue,
                rule.root_cause_code,
                rule.condition_text,
            ),
        )

        # LLM chỉ được hạ confidence khi thấy mâu thuẫn, không được nâng.
        if reply.get("contradicts") is True:
            decision.confidence = self.lower_confidence(decision.confidence, 0.75)
            decision.llm_flagged = True
            notes.append("LLM soát lần hai báo mâu thuẫn với rule đã chọn.")
        if reply.get("note"):
            notes.append(str(reply["note"])[:200])
        decision.notes = " ".join(notes)

        self.emit(case_file.case_id, "coordinator", "decision", decision.__dict__)
        return decision
