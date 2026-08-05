from __future__ import annotations

import json
import platform
import sys
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .agents import DeliveryAgent, OrderSellerAgent, PaymentAgent, PolicyAgent
from .config import AppConfig, DEFAULT_MODEL
from .data_store import DataStore
from .domain import (
    CaseRequest,
    OrderSellerHandoff,
    PaymentHandoff,
    PolicyDecision,
    money_float,
)
from .llm import GeminiPolicyReviewer, ReviewResult
from .trace import TraceWriter
from .validation import VerifierAgent, validate_output_directory


def build_output(
    case: CaseRequest,
    order_facts: OrderSellerHandoff,
    payment_facts: PaymentHandoff,
    decision: PolicyDecision,
) -> dict[str, Any]:
    order_id = case.order_id
    item_ids = [f"{order_id}:{item.item_id}" for item in order_facts.items][:5]
    seller_ids = list(order_facts.seller_ids[:5])
    payment_ids = [
        f"{order_id}:{payment.sequential}" for payment in payment_facts.payments
    ][:5]

    evidence = [f"order:{order_id}"]
    if decision.primary_issue not in {
        "canceled_order_paid",
        "unavailable_order_paid",
    }:
        evidence.extend(f"item:{item_id}" for item_id in item_ids)
    evidence.extend(f"payment:{payment_id}" for payment_id in payment_ids)
    # Seller master rows are evidence only when a seller is responsible.
    # For logistics, payment, canceled, unavailable and rejected claims, the
    # seller record adds no fact used by EC_POLICY_V1 and lowers evidence precision.
    if decision.primary_issue == "late_delivery_seller":
        evidence.extend(
            f"seller:{seller_id}"
            for seller_id in order_facts.late_seller_ids[:5]
        )
    policy_evidence = f"policy:{decision.cause_code}"
    evidence = evidence[:9] + [policy_evidence]

    return {
        "case_id": case.case_id,
        "assessment": {
            "primary_issue": decision.primary_issue,
            "case_status": decision.case_status,
            "confidence": decision.confidence,
        },
        "affected_entities": {
            "order_ids": [order_id],
            "item_ids": item_ids,
            "seller_ids": seller_ids,
            "payment_ids": payment_ids,
        },
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": decision.cause_code, "rank": 1}],
            "responsible_parties": [
                {"party_type": party_type, "party_id": party_id}
                for party_type, party_id in decision.responsible_parties[:3]
            ],
        },
        "evidence_ids": evidence,
        "financial_resolution": {
            "currency": "BRL",
            "item_total_brl": money_float(order_facts.item_total),
            "freight_total_brl": money_float(order_facts.freight_total),
            "payment_total_brl": money_float(payment_facts.payment_total),
            "recommended_refund_brl": money_float(decision.refund),
        },
        "resolution_actions": [decision.action],
    }


class DisputeResolutionPipeline:
    def __init__(self, config: AppConfig, with_llm: bool) -> None:
        self.config = config
        self.with_llm = with_llm
        self.config.validate(with_llm)

    def run(self, create_zip: bool = False) -> dict[str, Any]:
        started_at = datetime.now(timezone.utc)
        run_id = uuid.uuid4().hex
        cases = DataStore.load_cases(self.config.input_dir)
        store = DataStore(self.config.data_dir, cases)
        order_agent = OrderSellerAgent(store)
        payment_agent = PaymentAgent(store)
        delivery_agent = DeliveryAgent()
        policy_agent = PolicyAgent()
        verifier_agent = VerifierAgent(store)
        reviewer = GeminiPolicyReviewer(self.config) if self.with_llm else None

        self._prepare_output_dir()
        issue_counts: Counter[str] = Counter()
        llm_counts: Counter[str] = Counter()

        with TraceWriter(self.config.trace_path, run_id) as trace:
            for index, case in enumerate(cases, start=1):
                trace.write(
                    case_id=case.case_id,
                    agent="CoordinatorAgent",
                    event="case_received",
                    input_from="input_json",
                    payload={
                        "order_id": case.order_id,
                        "policy_version": case.policy_version,
                        "language": case.language,
                    },
                )

                order_facts = order_agent.analyze(case)
                trace.write(
                    case_id=case.case_id,
                    agent=order_agent.name,
                    event="handoff_completed",
                    input_from="CoordinatorAgent",
                    payload=order_facts.trace_payload(),
                )

                payment_facts = payment_agent.analyze(case, order_facts)
                trace.write(
                    case_id=case.case_id,
                    agent=payment_agent.name,
                    event="handoff_completed",
                    input_from="OrderSellerAgent",
                    payload=payment_facts.trace_payload(),
                )

                delivery_facts = delivery_agent.analyze(order_facts)
                trace.write(
                    case_id=case.case_id,
                    agent=delivery_agent.name,
                    event="handoff_completed",
                    input_from="OrderSellerAgent",
                    payload=delivery_facts.trace_payload(),
                )

                decision = policy_agent.analyze(
                    order_facts, payment_facts, delivery_facts
                )
                issue_counts[decision.primary_issue] += 1
                trace.write(
                    case_id=case.case_id,
                    agent=policy_agent.name,
                    event="decision_completed",
                    input_from="OrderSellerAgent,PaymentAgent,DeliveryAgent",
                    payload=decision.trace_payload(),
                )

                if reviewer:
                    review = reviewer.review(
                        case,
                        order_facts,
                        payment_facts,
                        delivery_facts,
                        decision,
                    )
                else:
                    review = ReviewResult("skipped", "not_requested", "", 0)
                llm_counts[review.status] += 1
                trace.write(
                    case_id=case.case_id,
                    agent=(reviewer.name if reviewer else "GeminiPolicyReviewerAgent"),
                    event="independent_review",
                    input_from="PolicyAgent",
                    payload=review.trace_payload(),
                )

                output = build_output(case, order_facts, payment_facts, decision)
                verifier_agent.verify(
                    case, output, order_facts, payment_facts, decision
                )
                trace.write(
                    case_id=case.case_id,
                    agent=verifier_agent.name,
                    event="verification_passed",
                    input_from="PolicyAgent",
                    payload={
                        "schema_valid": True,
                        "evidence_valid": True,
                        "financials_valid": True,
                    },
                )

                output_path = self.config.output_dir / f"{case.case_id}.json"
                self._write_json_atomic(output_path, output)
                trace.write(
                    case_id=case.case_id,
                    agent="CoordinatorAgent",
                    event="output_written",
                    input_from="VerifierAgent",
                    payload={"output_file": f"output/{output_path.name}"},
                )
                print(
                    f"[{index:02d}/50] {case.case_id}: {decision.primary_issue} "
                    f"(LLM {review.status})",
                    flush=True,
                )

        validate_output_directory(self.config.output_dir)
        zip_path = self._create_zip() if create_zip else None
        completed_at = datetime.now(timezone.utc)
        summary = {
            "run_id": run_id,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": round((completed_at - started_at).total_seconds(), 3),
            "case_count": len(cases),
            "output_count": len(list(self.config.output_dir.glob("EC_*.json"))),
            "issue_counts": dict(sorted(issue_counts.items())),
            "llm_review_counts": dict(sorted(llm_counts.items())),
            "zip_file": zip_path.name if zip_path else None,
        }
        self._write_metadata(summary)
        return summary

    def validate_existing_outputs(self) -> None:
        validate_output_directory(self.config.output_dir)

    def _prepare_output_dir(self) -> None:
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        unexpected = [
            path.name
            for path in self.config.output_dir.iterdir()
            if path.is_file() and not path.name.startswith("EC_")
        ]
        if unexpected:
            raise ValueError(f"Unexpected files in output/: {sorted(unexpected)}")
        for path in self.config.output_dir.glob("EC_*.json"):
            path.unlink()

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)

    def _create_zip(self) -> Path:
        zip_path = self.config.root / "output.zip"
        temp_path = self.config.root / "output.zip.tmp"
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(self.config.output_dir.glob("EC_*.json")):
                archive.write(path, arcname=path.name)
        temp_path.replace(zip_path)
        return zip_path

    def _write_metadata(self, summary: dict[str, Any]) -> None:
        metadata = {
            "project": "K3 Day 09 - Multi-Agent E-commerce Dispute Resolution",
            "pipeline_version": __version__,
            "policy_version": "EC_POLICY_V1",
            "model": {
                "provider": "Google",
                "name": DEFAULT_MODEL,
                "parameter_count": "not publicly disclosed",
                "assignment_limit": "<=10B parameters per agent",
                "compliance_status": "not independently verifiable",
                "temperature": self.config.temperature,
                "top_p": self.config.top_p,
                "max_output_tokens": min(self.config.max_output_tokens, 128),
            },
            "framework": {
                "name": "Custom Python multi-agent orchestration",
                "version": __version__,
                "dependencies": "Python standard library only",
            },
            "runtime": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "llm_review_enabled": self.with_llm,
                "api_key_env": "GOOGLE_API_KEY",
                "api_key_present": bool(self.config.google_api_key),
            },
            "agents": [
                "CoordinatorAgent",
                "OrderSellerAgent",
                "PaymentAgent",
                "DeliveryAgent",
                "PolicyAgent",
                "GeminiPolicyReviewerAgent",
                "VerifierAgent",
            ],
            "run": summary,
            "security": {
                "secrets_logged": False,
                "env_committed": False,
            },
        }
        self._write_json_atomic(self.config.metadata_path, metadata)
