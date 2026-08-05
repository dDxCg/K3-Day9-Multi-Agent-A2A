"""Entry point.

    python main.py                 # chạy cả 50 case, ghi output/ + trace.jsonl + metadata.json
    python main.py --case EC_001   # chạy 1 case để debug (không truncate trace)
    python main.py --no-llm        # deterministic-only, không gọi API
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.run_batch import run_batch

PROJECT_ROOT = Path(__file__).resolve().parent


def main() -> None:
    # Console Windows mặc định cp1252, không in được tiếng Việt trong log.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Multi-agent e-commerce dispute resolution")
    parser.add_argument("--case", help="chỉ chạy một case, ví dụ EC_001")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="tắt LLM, chỉ chạy tầng deterministic (dùng để kiểm tra rule engine)",
    )
    args = parser.parse_args()

    run_batch(PROJECT_ROOT, only_case=args.case, use_llm=not args.no_llm)


if __name__ == "__main__":
    main()
