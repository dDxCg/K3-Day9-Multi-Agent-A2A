from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from .data_store import DataStore
from .domain import CaseRequest, OrderSellerHandoff, PaymentHandoff, PolicyDecision, money
from .policy import ALLOWED_CAUSES, ALLOWED_ISSUES


EVIDENCE_PATTERN = re.compile(
    r"^(order:[0-9a-f]+|item:[0-9a-f]+:[0-9]+|payment:[0-9a-f]+:[0-9]+|"
    r"seller:[0-9a-f]+|policy:[A-Z_]+)$"
)

TOP_LEVEL_KEYS = {
    "case_id",
    "assessment",
    "affected_entities",
    "root_cause_analysis",
    "evidence_ids",
    "financial_resolution",
    "resolution_actions",
}


class OutputValidationError(ValueError):
    pass


class VerifierAgent:
    name = "VerifierAgent"

    def __init__(self, store: DataStore) -> None:
        self.store = store

    def verify(
        self,
        case: CaseRequest,
        output: dict[str, Any],
        order_facts: OrderSellerHandoff,
        payment_facts: PaymentHandoff,
        decision: PolicyDecision,
    ) -> None:
        errors: list[str] = []
        if set(output) != TOP_LEVEL_KEYS:
            errors.append("top-level schema keys mismatch")
        if output.get("case_id") != case.case_id:
            errors.append("case_id mismatch")

        assessment = output.get("assessment", {})
        if assessment.get("primary_issue") not in ALLOWED_ISSUES:
            errors.append("invalid primary_issue")
        if assessment.get("primary_issue") != decision.primary_issue:
            errors.append("primary_issue differs from policy decision")
        if assessment.get("case_status") not in {"action_required", "no_action"}:
            errors.append("invalid case_status")
        confidence = assessment.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            errors.append("confidence outside [0,1]")

        entities = output.get("affected_entities", {})
        for key in ("order_ids", "item_ids", "seller_ids", "payment_ids"):
            values = entities.get(key)
            if not isinstance(values, list) or len(values) > 5:
                errors.append(f"invalid entity set: {key}")
        if entities.get("order_ids") != [case.order_id]:
            errors.append("affected order_ids mismatch")

        root = output.get("root_cause_analysis", {})
        causes = root.get("ranked_causes", [])
        parties = root.get("responsible_parties", [])
        if not 1 <= len(causes) <= 3:
            errors.append("invalid ranked_causes count")
        elif causes[0].get("cause_code") not in ALLOWED_CAUSES:
            errors.append("invalid cause_code")
        if len(parties) > 3:
            errors.append("too many responsible parties")

        evidence = output.get("evidence_ids", [])
        if not isinstance(evidence, list) or len(evidence) > 10:
            errors.append("invalid evidence count")
        elif len(evidence) != len(set(evidence)):
            errors.append("duplicate evidence")
        else:
            for evidence_id in evidence:
                if not EVIDENCE_PATTERN.fullmatch(evidence_id):
                    errors.append(f"invalid evidence format: {evidence_id}")
                elif not self._evidence_exists(case, evidence_id):
                    errors.append(f"evidence does not exist: {evidence_id}")

        finance = output.get("financial_resolution", {})
        expected_amounts = {
            "item_total_brl": float(money(order_facts.item_total)),
            "freight_total_brl": float(money(order_facts.freight_total)),
            "payment_total_brl": float(money(payment_facts.payment_total)),
            "recommended_refund_brl": float(money(decision.refund)),
        }
        if finance.get("currency") != "BRL":
            errors.append("currency must be BRL")
        for key, expected in expected_amounts.items():
            actual = finance.get(key)
            if not isinstance(actual, (int, float)) or Decimal(str(actual)) != Decimal(str(expected)):
                errors.append(f"financial mismatch: {key}")

        actions = output.get("resolution_actions", [])
        if actions != [decision.action] or len(actions) > 5:
            errors.append("resolution_actions mismatch")

        if errors:
            raise OutputValidationError(f"{case.case_id}: " + "; ".join(errors))

    def _evidence_exists(self, case: CaseRequest, evidence_id: str) -> bool:
        kind, remainder = evidence_id.split(":", 1)
        if kind == "order":
            return remainder == case.order_id and remainder in self.store.orders
        if kind == "item":
            order_id, item_id = remainder.rsplit(":", 1)
            return order_id == case.order_id and any(
                item.item_id == item_id for item in self.store.get_items(order_id)
            )
        if kind == "payment":
            order_id, sequence = remainder.rsplit(":", 1)
            return order_id == case.order_id and any(
                payment.sequential == sequence
                for payment in self.store.get_payments(order_id)
            )
        if kind == "seller":
            return remainder in self.store.seller_ids and any(
                item.seller_id == remainder
                for item in self.store.get_items(case.order_id)
            )
        if kind == "policy":
            return remainder in ALLOWED_CAUSES
        return False


def validate_output_directory(output_dir: Path) -> None:
    expected = {f"EC_{index:03d}.json" for index in range(1, 51)}
    actual = {path.name for path in output_dir.iterdir() if path.is_file()}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise OutputValidationError(
            f"Output directory mismatch; missing={missing}, extra={extra}"
        )
    for path in output_dir.glob("EC_*.json"):
        json.loads(path.read_text(encoding="utf-8"))
