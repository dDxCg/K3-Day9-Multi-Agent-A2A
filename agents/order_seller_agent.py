"""Order & Seller Agent — trạng thái đơn, item, seller và mốc bàn giao.

Quyền truy cập: orders.csv, order_items.csv, sellers.csv (read-only).
"""

from __future__ import annotations

from agents.base import BaseAgent
from data_access import evidence as ev
from llm import prompts
from schema.case_file import CaseFile, OrderSellerFindings


class OrderSellerAgent(BaseAgent):
    name = "order_seller_agent"

    def run(self, case_file: CaseFile) -> OrderSellerFindings:
        order_id = case_file.claimed_order_id
        order = self.store.get_order(order_id)

        if order is None:
            findings = OrderSellerFindings(
                order_found=False,
                notes="Không tìm thấy order_id trong orders.csv.",
            )
            self.emit(case_file.case_id, "coordinator", "finding", findings.__dict__)
            return findings

        items = self.store.get_items(order_id)
        item_total, freight_total, _ = self.store.totals(order_id)

        carrier_dt = order.delivered_carrier_dt
        seller_ids: list[str] = []
        late_handoff: list[str] = []
        for item in items:
            if item.seller_id and item.seller_id not in seller_ids:
                seller_ids.append(item.seller_id)
            # Seller bị coi là bàn giao muộn nếu carrier nhận hàng sau
            # shipping_limit_date CỦA ITEM THUỘC SELLER ĐÓ (README mục 4).
            if (
                carrier_dt is not None
                and item.shipping_limit_dt is not None
                and carrier_dt > item.shipping_limit_dt
                and item.seller_id
                and item.seller_id not in late_handoff
            ):
                late_handoff.append(item.seller_id)

        # Seller vi phạm được ưu tiên trong evidence vì chúng chống lưng cho
        # kết luận trách nhiệm; seller còn lại chỉ bổ sung nếu còn chỗ.
        ordered_sellers = late_handoff + [s for s in seller_ids if s not in late_handoff]
        seller_evidence = [
            ev.seller_evidence(sid) for sid in ordered_sellers if self.store.get_seller(sid)
        ]

        findings = OrderSellerFindings(
            order_found=True,
            order_status=order.order_status,
            order_delivered_carrier_date=order.delivered_carrier_date,
            order_delivered_customer_date=order.delivered_customer_date,
            order_estimated_delivery_date=order.estimated_delivery_date,
            item_count=len(items),
            item_total_brl=item_total,
            freight_total_brl=freight_total,
            seller_ids=ordered_sellers,
            late_handoff_seller_ids=late_handoff,
            item_entity_ids=[item.entity_id for item in items],
            evidence_ids=[order.evidence_id]
            + [item.evidence_id for item in items]
            + seller_evidence,
        )

        if carrier_dt is None and items:
            findings.notes = (
                "Thiếu order_delivered_carrier_date — không đủ căn cứ kết luận seller bàn giao muộn."
            )

        llm_facts = {
            "order_status": findings.order_status,
            "delivered_carrier_date": findings.order_delivered_carrier_date,
            "item_count": findings.item_count,
            "shipping_limits": [
                {"order_item_id": i.order_item_id, "seller_id": i.seller_id,
                 "shipping_limit_date": i.shipping_limit_date}
                for i in items[:5]
            ],
            "late_handoff_seller_ids": late_handoff,
        }
        reply = self.ask(
            prompts.ORDER_SELLER_SYSTEM,
            prompts.order_seller_user(case_file.case_id, llm_facts),
        )
        if reply.get("note"):
            findings.notes = str(reply["note"])[:300]

        self.emit(case_file.case_id, "coordinator", "finding", findings.__dict__)
        return findings
