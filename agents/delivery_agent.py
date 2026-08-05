"""Delivery Agent — so thời điểm giao thực tế với hạn giao dự kiến.

Quyền truy cập: orders.csv (read-only).
"""

from __future__ import annotations

from agents.base import BaseAgent
from llm import prompts
from schema.case_file import CaseFile, DeliveryFindings


class DeliveryAgent(BaseAgent):
    name = "delivery_agent"

    def run(self, case_file: CaseFile) -> DeliveryFindings:
        order = self.store.get_order(case_file.claimed_order_id)

        if order is None:
            findings = DeliveryFindings(notes="Không tìm thấy order_id trong orders.csv.")
            self.emit(case_file.case_id, "coordinator", "finding", findings.__dict__)
            return findings

        delivered_dt = order.delivered_customer_dt
        estimated_dt = order.estimated_delivery_dt
        # So sánh trực tiếp giá trị timestamp trong CSV, không đổi múi giờ.
        late_overall = bool(
            delivered_dt is not None and estimated_dt is not None and delivered_dt > estimated_dt
        )

        findings = DeliveryFindings(
            delivered=delivered_dt is not None,
            late_overall=late_overall,
            delivered_customer_date=order.delivered_customer_date,
            estimated_delivery_date=order.estimated_delivery_date,
            evidence_ids=[order.evidence_id],
        )
        if delivered_dt is None:
            findings.notes = (
                f"Đơn chưa có mốc giao cho khách (order_status={order.order_status})."
            )

        reply = self.ask(
            prompts.DELIVERY_SYSTEM,
            prompts.delivery_user(
                case_file.case_id,
                {
                    "delivered_customer_date": findings.delivered_customer_date,
                    "estimated_delivery_date": findings.estimated_delivery_date,
                    "late_overall": late_overall,
                    "order_status": order.order_status,
                },
            ),
        )
        if reply.get("note"):
            findings.notes = str(reply["note"])[:300]

        self.emit(case_file.case_id, "coordinator", "finding", findings.__dict__)
        return findings
