from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import AppConfig
from .pipeline import DisputeResolutionPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run and validate the Olist multi-agent dispute pipeline"
    )
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="Call Gemini once per case for independent policy review",
    )
    parser.add_argument(
        "--zip",
        action="store_true",
        help="Create output.zip containing exactly the 50 JSON outputs",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the existing output directory without running cases",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    config = AppConfig.from_root(root)
    pipeline = DisputeResolutionPipeline(config, with_llm=args.with_llm)

    if args.validate_only:
        result = pipeline.validate_existing_outputs()
        print(
            "Output validation passed: "
            f"{result['validated_case_count']} files match source data and EC_POLICY_V1"
        )
        return 0

    summary = pipeline.run(create_zip=args.zip)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0
