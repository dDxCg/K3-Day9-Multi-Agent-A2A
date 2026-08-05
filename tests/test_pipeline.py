from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dispute_resolution.agents import (  # noqa: E402
    DeliveryAgent,
    OrderSellerAgent,
    PaymentAgent,
    PolicyAgent,
)
from dispute_resolution.config import AppConfig  # noqa: E402
from dispute_resolution.data_store import DataStore  # noqa: E402
from dispute_resolution.pipeline import DisputeResolutionPipeline  # noqa: E402
from dispute_resolution.validation import OutputValidationError  # noqa: E402


EXPECTED_COUNTS = {
    "canceled_order_paid": 8,
    "late_delivery_logistics": 8,
    "late_delivery_seller": 8,
    "unavailable_order_paid": 8,
    "unsupported_late_claim": 9,
    "valid_split_payment": 9,
}


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = DataStore.load_cases(ROOT / "input")
        cls.store = DataStore(ROOT / "data", cls.cases)

    def test_policy_distribution_matches_dataset(self) -> None:
        order_agent = OrderSellerAgent(self.store)
        payment_agent = PaymentAgent(self.store)
        delivery_agent = DeliveryAgent()
        policy_agent = PolicyAgent()
        counts: Counter[str] = Counter()

        for case in self.cases:
            order = order_agent.analyze(case)
            payment = payment_agent.analyze(case, order)
            delivery = delivery_agent.analyze(order)
            decision = policy_agent.analyze(order, payment, delivery)
            counts[decision.primary_issue] += 1

        self.assertEqual(dict(sorted(counts.items())), EXPECTED_COUNTS)

    def test_full_pipeline_without_llm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            output_dir = temp_root / "output"
            trace_path = temp_root / "logging" / "trace.jsonl"
            metadata_path = temp_root / "logging" / "metadata.json"
            config = AppConfig.from_root(ROOT)
            test_config = AppConfig(
                root=temp_root,
                data_dir=config.data_dir,
                input_dir=config.input_dir,
                output_dir=output_dir,
                trace_path=trace_path,
                metadata_path=metadata_path,
                env_path=config.env_path,
                model=config.model,
                google_api_key=config.google_api_key,
                temperature=config.temperature,
                top_p=config.top_p,
                max_output_tokens=config.max_output_tokens,
                request_timeout_seconds=config.request_timeout_seconds,
                max_retries=config.max_retries,
                request_delay_seconds=config.request_delay_seconds,
            )
            pipeline = DisputeResolutionPipeline(test_config, with_llm=False)
            summary = pipeline.run(create_zip=True)

            self.assertEqual(summary["case_count"], 50)
            self.assertEqual(summary["output_count"], 50)
            self.assertEqual(summary["issue_counts"], EXPECTED_COUNTS)
            self.assertEqual(len(list(output_dir.glob("EC_*.json"))), 50)
            validation = pipeline.validate_existing_outputs()
            self.assertEqual(validation["validated_case_count"], 50)
            self.assertEqual(validation["validation_error_count"], 0)

            expected_zip_names = [f"EC_{index:03d}.json" for index in range(1, 51)]
            with zipfile.ZipFile(temp_root / "output.zip") as archive:
                self.assertEqual(archive.namelist(), expected_zip_names)
                for name in expected_zip_names:
                    self.assertEqual(
                        archive.read(name), (output_dir / name).read_bytes()
                    )
            sample = json.loads((output_dir / "EC_001.json").read_text("utf-8"))
            self.assertEqual(sample["assessment"]["primary_issue"], "late_delivery_seller")
            self.assertEqual(sample["assessment"]["confidence"], 0.99)

            logistics = json.loads(
                (output_dir / "EC_009.json").read_text("utf-8")
            )
            self.assertFalse(
                any(
                    evidence.startswith("seller:")
                    for evidence in logistics["evidence_ids"]
                )
            )

            split_payment = json.loads(
                (output_dir / "EC_025.json").read_text("utf-8")
            )
            self.assertFalse(
                any(
                    evidence.startswith("seller:")
                    for evidence in split_payment["evidence_ids"]
                )
            )

            canceled = json.loads(
                (output_dir / "EC_003.json").read_text("utf-8")
            )
            self.assertTrue(canceled["affected_entities"]["item_ids"])
            self.assertTrue(
                any(
                    evidence.startswith("item:")
                    for evidence in canceled["evidence_ids"]
                )
            )
            self.assertFalse(
                any(
                    evidence.startswith("seller:")
                    for evidence in canceled["evidence_ids"]
                )
            )

            trace_records = [
                json.loads(line)
                for line in trace_path.read_text("utf-8").splitlines()
            ]
            self.assertEqual(len(trace_records), 400)
            output_events = [
                record
                for record in trace_records
                if record["event"] == "output_written"
            ]
            self.assertEqual(len(output_events), 50)
            for record in output_events:
                payload = record["payload"]
                output_path = output_dir / Path(payload["output_file"]).name
                self.assertEqual(
                    payload["output_sha256"],
                    hashlib.sha256(output_path.read_bytes()).hexdigest(),
                )
                self.assertFalse(payload["llm_modified_output"])
                self.assertTrue(payload["resolution_actions"])

            metadata = json.loads(metadata_path.read_text("utf-8"))
            provenance = metadata["output_provenance"]
            self.assertEqual(provenance["write_mode"], "code_only_atomic")
            self.assertFalse(provenance["llm_can_modify_output"])
            self.assertEqual(len(provenance["output_digest_sha256"]), 64)

            tampered = json.loads(
                (output_dir / "EC_001.json").read_text(encoding="utf-8")
            )
            tampered["financial_resolution"]["recommended_refund_brl"] = 0.0
            (output_dir / "EC_001.json").write_text(
                json.dumps(tampered, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(OutputValidationError):
                pipeline.validate_existing_outputs()

    def test_priority_canceled_over_delivery_fields(self) -> None:
        case = next(case for case in self.cases if case.case_id == "EC_008")
        order_agent = OrderSellerAgent(self.store)
        payment_agent = PaymentAgent(self.store)
        order = order_agent.analyze(case)
        payment = payment_agent.analyze(case, order)
        delivery = DeliveryAgent().analyze(order)
        decision = PolicyAgent().analyze(order, payment, delivery)
        self.assertEqual(decision.primary_issue, "canceled_order_paid")

    def test_unavailable_order_without_items(self) -> None:
        case = next(case for case in self.cases if case.case_id == "EC_005")
        order = OrderSellerAgent(self.store).analyze(case)
        self.assertEqual(order.items, ())
        self.assertEqual(float(order.item_total), 0.0)
        self.assertEqual(float(order.freight_total), 0.0)


if __name__ == "__main__":
    unittest.main()
