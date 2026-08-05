"""Payment Agent — đối soát tổng payment với tổng item + freight.

Quyền truy cập: order_payments.csv (read-only).
Nhận item_total + freight_total từ Order & Seller Agent qua Coordinator.
"""

from __future__ import annotations

from agents.base import BaseAgent
from data_access.policy_table import RECONCILE_TOLERANCE_BRL
from llm import prompts
from schema.case_file import CaseFile, PaymentFindings


class PaymentAgent(BaseAgent):
    name = "payment_agent"

    def run(
        self, case_file: CaseFile, item_total_brl: float, freight_total_brl: float
    ) -> PaymentFindings:
        order_id = case_file.claimed_order_id
        payments = self.store.get_payments(order_id)

        payment_total = round(sum(p.payment_value for p in payments), 2)
        expected = round(item_total_brl + freight_total_brl, 2)
        delta = round(payment_total - expected, 2)
        reconciled = abs(delta) <= RECONCILE_TOLERANCE_BRL

        findings = PaymentFindings(
            payment_row_count=len(payments),
            payment_total_brl=payment_total,
            reconciled=reconciled,
            delta_brl=delta,
            payment_entity_ids=[p.entity_id for p in payments],
            evidence_ids=[p.evidence_id for p in payments],
        )

        reply = self.ask(
            prompts.PAYMENT_SYSTEM,
            prompts.payment_user(
                case_file.case_id,
                {
                    "payment_row_count": findings.payment_row_count,
                    "payment_total_brl": payment_total,
                    "expected_item_plus_freight_brl": expected,
                    "delta_brl": delta,
                    "tolerance_brl": RECONCILE_TOLERANCE_BRL,
                    "reconciled": reconciled,
                },
            ),
        )
        if reply.get("note"):
            findings.notes = str(reply["note"])[:300]

        self.emit(case_file.case_id, "coordinator", "finding", findings.__dict__)
        return findings
