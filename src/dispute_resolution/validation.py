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

ASSESSMENT_KEYS = {"primary_issue", "case_status", "confidence"}
ENTITY_KEYS = {"order_ids", "item_ids", "seller_ids", "payment_ids"}
ROOT_CAUSE_KEYS = {"ranked_causes", "responsible_parties"}
FINANCIAL_KEYS = {
    "currency",
    "item_total_brl",
    "freight_total_brl",
    "payment_total_brl",
    "recommended_refund_brl",
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

        assessment = self._nested_object(
            output, "assessment", ASSESSMENT_KEYS, errors
        )
        if assessment.get("primary_issue") not in ALLOWED_ISSUES:
            errors.append("invalid primary_issue")
        if assessment.get("primary_issue") != decision.primary_issue:
            errors.append("primary_issue differs from policy decision")
        if assessment.get("case_status") not in {"action_required", "no_action"}:
            errors.append("invalid case_status")
        elif assessment.get("case_status") != decision.case_status:
            errors.append("case_status differs from policy decision")
        confidence = assessment.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            errors.append("confidence outside [0,1]")
        elif confidence != decision.confidence:
            errors.append("confidence differs from policy decision")

        entities = self._nested_object(
            output, "affected_entities", ENTITY_KEYS, errors
        )
        expected_entities = {
            "order_ids": [case.order_id],
            "item_ids": [
                f"{case.order_id}:{item.item_id}" for item in order_facts.items[:5]
            ],
            "seller_ids": list(order_facts.seller_ids[:5]),
            "payment_ids": [
                f"{case.order_id}:{payment.sequential}"
                for payment in payment_facts.payments[:5]
            ],
        }
        for key in ("order_ids", "item_ids", "seller_ids", "payment_ids"):
            values = entities.get(key)
            if not isinstance(values, list) or len(values) > 5:
                errors.append(f"invalid entity set: {key}")
            elif values != expected_entities[key]:
                errors.append(f"affected entities mismatch: {key}")

        root = self._nested_object(
            output, "root_cause_analysis", ROOT_CAUSE_KEYS, errors
        )
        causes = root.get("ranked_causes", [])
        parties = root.get("responsible_parties", [])
        expected_causes = [{"cause_code": decision.cause_code, "rank": 1}]
        if not isinstance(causes, list) or not 1 <= len(causes) <= 3:
            errors.append("invalid ranked_causes count")
        elif not isinstance(causes[0], dict):
            errors.append("invalid ranked_causes entry")
        elif causes[0].get("cause_code") not in ALLOWED_CAUSES:
            errors.append("invalid cause_code")
        elif causes != expected_causes:
            errors.append("ranked_causes differ from policy decision")
        if not isinstance(parties, list):
            errors.append("responsible_parties must be a list")
            parties = []
        elif len(parties) > 3:
            errors.append("too many responsible parties")
        expected_parties = [
            {"party_type": party_type, "party_id": party_id}
            for party_type, party_id in decision.responsible_parties[:3]
        ]
        if parties != expected_parties:
            errors.append("responsible_parties differ from policy decision")

        evidence = output.get("evidence_ids", [])
        if not isinstance(evidence, list) or len(evidence) > 10:
            errors.append("invalid evidence count")
        elif len(evidence) != len(set(evidence)):
            errors.append("duplicate evidence")
        else:
            for evidence_id in evidence:
                if not isinstance(evidence_id, str):
                    errors.append("evidence ID must be a string")
                elif not EVIDENCE_PATTERN.fullmatch(evidence_id):
                    errors.append(f"invalid evidence format: {evidence_id}")
                elif not self._evidence_exists(case, evidence_id):
                    errors.append(f"evidence does not exist: {evidence_id}")
            expected_evidence = [f"order:{case.order_id}"]
            expected_evidence.extend(
                f"item:{item_id}" for item_id in expected_entities["item_ids"]
            )
            expected_evidence.extend(
                f"payment:{payment_id}"
                for payment_id in expected_entities["payment_ids"]
            )
            if decision.primary_issue == "late_delivery_seller":
                expected_evidence.extend(
                    f"seller:{seller_id}"
                    for seller_id in order_facts.late_seller_ids[:5]
                )
            expected_evidence = expected_evidence[:9] + [
                f"policy:{decision.cause_code}"
            ]
            if evidence != expected_evidence:
                errors.append("evidence differs from provenance contract")

        finance = self._nested_object(
            output, "financial_resolution", FINANCIAL_KEYS, errors
        )
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

    @staticmethod
    def _nested_object(
        output: dict[str, Any],
        key: str,
        expected_keys: set[str],
        errors: list[str],
    ) -> dict[str, Any]:
        value = output.get(key)
        if not isinstance(value, dict):
            errors.append(f"{key} must be an object")
            return {}
        if set(value) != expected_keys:
            errors.append(f"{key} schema keys mismatch")
        return value

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
    if not output_dir.is_dir():
        raise OutputValidationError(f"Output directory not found: {output_dir}")
    expected = {f"EC_{index:03d}.json" for index in range(1, 51)}
    actual = {path.name for path in output_dir.iterdir()}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise OutputValidationError(
            f"Output directory mismatch; missing={missing}, extra={extra}"
        )
    for path in sorted(output_dir.glob("EC_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise OutputValidationError(f"{path.name}: top-level JSON must be an object")
