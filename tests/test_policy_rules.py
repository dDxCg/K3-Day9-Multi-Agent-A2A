"""Unit test rule engine tất định của Policy Agent — không gọi LLM, không đọc CSV.

Chỉ test các hàm thuần (`_classify`, `_refund_brl`, `_evidence`) với state/finding
dựng tay, phủ đủ 6 nhánh EC_POLICY_V1 theo đúng thứ tự ưu tiên trong README mục 4.

    py -3 -m unittest tests.test_policy_rules -v
"""

from __future__ import annotations

import unittest

from src.agents.policy import _classify, _evidence, _refund_brl
from src.schema import MAX_EVIDENCE


def _state(*, status="delivered", payment_total=0.0, late=False, late_sellers=None,
           n_payments=1, reconciled=True, is_split=False):
    late_sellers = late_sellers or []
    return {
        "findings": {
            "order_seller": {
                "order_status": status,
                "late_handoff_seller_ids": late_sellers,
            },
            "payment": {
                "payment_total_brl": payment_total,
                "is_split_payment": is_split,
                "reconciled": reconciled,
                "n_payment_rows": n_payments,
            },
            "delivery": {"late_vs_estimate": late},
        }
    }


class ClassifyTests(unittest.TestCase):
    def test_canceled_order_paid_wins_over_everything(self) -> None:
        # canceled + đã trả tiền phải thắng dù cũng late + split hợp lệ.
        state = _state(status="canceled", payment_total=50.0, late=True,
                        late_sellers=["s1"], is_split=True, reconciled=True)
        self.assertEqual(_classify(state), "canceled_order_paid")

    def test_unavailable_order_paid(self) -> None:
        state = _state(status="unavailable", payment_total=30.0)
        self.assertEqual(_classify(state), "unavailable_order_paid")

    def test_unavailable_without_payment_falls_through(self) -> None:
        # unavailable nhưng payment_total = 0 -> không khớp rule 2, rơi xuống fallback.
        state = _state(status="unavailable", payment_total=0.0)
        self.assertEqual(_classify(state), "unsupported_late_claim")

    def test_late_delivery_seller_beats_late_delivery_logistics(self) -> None:
        state = _state(status="delivered", late=True, late_sellers=["seller_a"])
        self.assertEqual(_classify(state), "late_delivery_seller")

    def test_late_delivery_logistics_when_no_seller_at_fault(self) -> None:
        state = _state(status="delivered", late=True, late_sellers=[])
        self.assertEqual(_classify(state), "late_delivery_logistics")

    def test_valid_split_payment(self) -> None:
        state = _state(status="delivered", late=False, is_split=True, reconciled=True)
        self.assertEqual(_classify(state), "valid_split_payment")

    def test_split_payment_not_reconciled_falls_through(self) -> None:
        state = _state(status="delivered", late=False, is_split=True, reconciled=False)
        self.assertEqual(_classify(state), "unsupported_late_claim")

    def test_unsupported_late_claim_is_default_fallback(self) -> None:
        state = _state(status="delivered", late=False, is_split=False)
        self.assertEqual(_classify(state), "unsupported_late_claim")


class RefundTests(unittest.TestCase):
    VIEW = {"totals": {"payment_total_brl": 120.5, "freight_total_brl": 15.25, "item_total_brl": 100.0}}

    def test_full_refund_for_canceled(self) -> None:
        self.assertEqual(_refund_brl("canceled_order_paid", self.VIEW), 120.5)

    def test_full_refund_for_unavailable(self) -> None:
        self.assertEqual(_refund_brl("unavailable_order_paid", self.VIEW), 120.5)

    def test_freight_only_for_late_seller(self) -> None:
        self.assertEqual(_refund_brl("late_delivery_seller", self.VIEW), 15.25)

    def test_freight_only_for_late_logistics(self) -> None:
        self.assertEqual(_refund_brl("late_delivery_logistics", self.VIEW), 15.25)

    def test_no_refund_for_valid_split_payment(self) -> None:
        self.assertEqual(_refund_brl("valid_split_payment", self.VIEW), 0.0)

    def test_no_refund_for_unsupported_claim(self) -> None:
        self.assertEqual(_refund_brl("unsupported_late_claim", self.VIEW), 0.0)


class EvidenceTests(unittest.TestCase):
    def test_capped_at_max_evidence_and_deduped(self) -> None:
        item_ids = [f"ORDER1:{i}" for i in range(1, 8)]
        payment_ids = [f"ORDER1:{i}" for i in range(1, 8)]
        ev = _evidence(
            "late_delivery_seller",
            "SELLER_HANDOFF_AFTER_LIMIT",
            "ORDER1",
            item_ids,
            ["seller_x"],
            ["seller_x"],
            payment_ids,
        )
        self.assertLessEqual(len(ev), MAX_EVIDENCE)
        self.assertEqual(len(ev), len(set(ev)))  # không trùng lặp

    def test_head_always_has_order_and_policy(self) -> None:
        ev = _evidence(
            "unsupported_late_claim", "DELIVERY_WITHIN_ESTIMATE", "ORDER2", [], [], [], [],
        )
        self.assertEqual(ev[0], "order:ORDER2")
        self.assertEqual(ev[1], "policy:DELIVERY_WITHIN_ESTIMATE")


if __name__ == "__main__":
    unittest.main()
