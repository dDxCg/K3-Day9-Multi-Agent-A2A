"""Policy Agent — áp EC_POLICY_V1.

Rule engine chạy first-match-wins theo ĐÚNG thứ tự bảng README mục 4; LLM chỉ
được đề xuất root cause phụ và nhích confidence. Model không có quyền đổi
primary_issue vì đó là 20% điểm và một model 8B không đáng tin ở chỗ này.
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
                policy_table.get_policy_table(),
                sorted(policy_table.ROOT_CAUSE_CODES),
            ),
        )

        # Chỉ nhận cause_code hợp lệ, khác cause chính, tối đa 2 cái.
        secondary: list[str] = []
        for code in reply.get("secondary_causes") or []:
            code = str(code).strip()
            if (
                code in policy_table.ROOT_CAUSE_CODES
                and code != rule.root_cause_code
                and code not in secondary
            ):
                secondary.append(code)
        decision.secondary_causes = secondary[:2]

        if reply.get("confidence") is not None:
            llm_conf = self.clamp_confidence(reply["confidence"], confidence)
            decision.confidence = self.blend_confidence(confidence, llm_conf)
        if reply.get("note"):
            decision.notes = (decision.notes + " " + str(reply["note"])[:200]).strip()

        self.emit(case_file.case_id, "coordinator", "decision", decision.__dict__)
        return decision
