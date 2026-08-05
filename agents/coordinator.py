"""Coordinator — hub điều phối.

Mọi agent chỉ nói chuyện với Coordinator, không nói thẳng với nhau. Thứ tự gọi
cố định 1→5 vì Payment cần tổng item+freight từ Order&Seller, và Policy cần kết
quả của cả ba agent domain.
"""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from agents.delivery_agent import DeliveryAgent
from agents.order_seller_agent import OrderSellerAgent
from agents.payment_agent import PaymentAgent
from agents.policy_agent import PolicyAgent
from agents.verifier_agent import VerifierAgent
from data_access import evidence as ev
from data_access.data_store import DataStore
from llm.client import LLMClient
from pipeline.trace import TraceWriter
from schema.case_file import CaseFile
from schema.output_schema import CaseOutput, MAX_ENTITY_IDS

MAX_VERIFY_ATTEMPTS = 2


class Coordinator(BaseAgent):
    name = "coordinator"

    def __init__(self, store: DataStore, llm: LLMClient, trace: TraceWriter):
        super().__init__(store, llm, trace)
        self.order_seller = OrderSellerAgent(store, llm, trace)
        self.delivery = DeliveryAgent(store, llm, trace)
        self.payment = PaymentAgent(store, llm, trace)
        self.policy = PolicyAgent(store, llm, trace)
        self.verifier = VerifierAgent(store, llm, trace)

    def handle(self, case_input: dict[str, Any]) -> CaseOutput:
        request = case_input.get("customer_request", {})
        case_file = CaseFile(
            case_id=case_input["case_id"],
            claimed_order_id=request.get("claimed_order_id", ""),
            opened_at=case_input.get("opened_at"),
            policy_version=case_input.get("policy_version"),
            customer_message=request.get("message", ""),
        )

        # 1 — Order & Seller
        self.emit(case_file.case_id, "order_seller_agent", "request",
                  {"claimed_order_id": case_file.claimed_order_id})
        case_file.order_seller = self.order_seller.run(case_file)

        # 2 — Delivery
        self.emit(case_file.case_id, "delivery_agent", "request",
                  {"claimed_order_id": case_file.claimed_order_id})
        case_file.delivery = self.delivery.run(case_file)

        # 3 — Payment (cần tổng item+freight từ bước 1)
        self.emit(case_file.case_id, "payment_agent", "request", {
            "claimed_order_id": case_file.claimed_order_id,
            "item_total_brl": case_file.order_seller.item_total_brl,
            "freight_total_brl": case_file.order_seller.freight_total_brl,
        })
        case_file.payment = self.payment.run(
            case_file,
            case_file.order_seller.item_total_brl,
            case_file.order_seller.freight_total_brl,
        )

        # 4 & 5 — Policy rồi Verifier, tối đa 1 lần retry
        draft: dict[str, Any] = {}
        retry_errors: list[str] | None = None
        for attempt in range(1, MAX_VERIFY_ATTEMPTS + 1):
            self.emit(case_file.case_id, "policy_agent", "request", case_file.facts_for_policy())
            case_file.policy = self.policy.run(case_file, retry_errors=retry_errors)

            draft = self._build_draft(case_file)
            self.emit(case_file.case_id, "verifier_agent", "request", draft)
            case_file.verification = self.verifier.run(case_file, draft, attempt=attempt)

            if case_file.verification.passed:
                break
            retry_errors = case_file.verification.errors

        if not case_file.verification.passed:
            # Fail lần 2 → fallback an toàn, thà confidence thấp còn hơn ghi
            # JSON sai schema (hard gate = 0 điểm).
            draft = self._safe_fallback(case_file)
            self.emit(case_file.case_id, "coordinator", "fallback",
                      {"errors": case_file.verification.errors})
        elif case_file.verification.warnings:
            # Kết luận vẫn giữ nguyên (dữ liệu thắng lời khiếu nại), nhưng
            # hạ confidence để phản ánh đúng mức chắc chắn.
            draft["assessment"]["confidence"] = self.lower_confidence(
                draft["assessment"]["confidence"], 0.85
            )
            self.emit(case_file.case_id, "coordinator", "warning",
                      {"warnings": case_file.verification.warnings})

        output = CaseOutput.model_validate(draft)
        case_file.final_output = output.model_dump()
        self.emit(case_file.case_id, "output", "final", case_file.final_output)
        return output

    # ------------------------------------------------------------------ dựng draft

    def _build_draft(self, case_file: CaseFile) -> dict[str, Any]:
        os_f = case_file.order_seller
        pm_f = case_file.payment
        decision = case_file.policy
        order_id = case_file.claimed_order_id

        order = self.store.get_order(order_id)
        items = self.store.get_items(order_id)
        payments = self.store.get_payments(order_id)

        # Mỗi case trong bộ này map sạch vào đúng một rule, nên chỉ có một root
        # cause. Không độn thêm cause phụ — cause thừa chỉ làm giảm precision.
        ranked_causes = [{"cause_code": decision.root_cause_code, "rank": 1}]

        seller_evidence = [
            ev.seller_evidence(sid)
            for sid in os_f.seller_ids
            if self.store.get_seller(sid)
        ]

        return {
            "case_id": case_file.case_id,
            "assessment": {
                "primary_issue": decision.primary_issue,
                "case_status": decision.case_status,
                "confidence": decision.confidence,
            },
            "affected_entities": {
                "order_ids": [order_id] if order else [],
                "item_ids": [i.entity_id for i in items][:MAX_ENTITY_IDS],
                "seller_ids": os_f.seller_ids[:MAX_ENTITY_IDS],
                "payment_ids": [p.entity_id for p in payments][:MAX_ENTITY_IDS],
            },
            "root_cause_analysis": {
                "ranked_causes": ranked_causes,
                "responsible_parties": decision.responsible_parties,
            },
            "evidence_ids": ev.assemble(
                order_ev=order.evidence_id if order else None,
                item_evs=[i.evidence_id for i in items],
                payment_evs=[p.evidence_id for p in payments],
                seller_evs=seller_evidence,
                policy_ev=ev.policy_evidence(decision.root_cause_code),
            ),
            "financial_resolution": {
                "currency": "BRL",
                "item_total_brl": os_f.item_total_brl,
                "freight_total_brl": os_f.freight_total_brl,
                "payment_total_brl": pm_f.payment_total_brl,
                "recommended_refund_brl": decision.recommended_refund_brl,
            },
            "resolution_actions": decision.resolution_actions,
        }

    def _safe_fallback(self, case_file: CaseFile) -> dict[str, Any]:
        """Kết luận tối thiểu, dựng lại hoàn toàn từ CSV, confidence thấp."""
        order_id = case_file.claimed_order_id
        order = self.store.get_order(order_id)
        item_total, freight_total, payment_total = self.store.totals(order_id)
        cause = "DELIVERY_WITHIN_ESTIMATE"

        return {
            "case_id": case_file.case_id,
            "assessment": {
                "primary_issue": "unsupported_late_claim",
                "case_status": "no_action",
                "confidence": 0.2,
            },
            "affected_entities": {
                "order_ids": [order_id] if order else [],
                "item_ids": [],
                "seller_ids": [],
                "payment_ids": [],
            },
            "root_cause_analysis": {
                "ranked_causes": [{"cause_code": cause, "rank": 1}],
                "responsible_parties": [],
            },
            "evidence_ids": ev.assemble(
                order_ev=order.evidence_id if order else None,
                item_evs=[],
                payment_evs=[],
                seller_evs=[],
                policy_ev=ev.policy_evidence(cause),
            ),
            "financial_resolution": {
                "currency": "BRL",
                "item_total_brl": item_total,
                "freight_total_brl": freight_total,
                "payment_total_brl": payment_total,
                "recommended_refund_brl": 0.0,
            },
            "resolution_actions": ["reject_late_refund"],
        }
