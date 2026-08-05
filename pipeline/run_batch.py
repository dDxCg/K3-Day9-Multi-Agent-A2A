"""Chạy toàn bộ input/ → output/, đồng thời ghi trace.jsonl và metadata.json."""

from __future__ import annotations

import json
import platform
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from agents.coordinator import Coordinator
from data_access.data_store import DataStore
from llm import client as llm_module
from llm.client import LLMClient
from pipeline.run_case import load_case, run_case, write_output
from pipeline.trace import TraceWriter


def run_batch(
    project_root: str | Path,
    only_case: str | None = None,
    use_llm: bool = True,
) -> dict[str, int]:
    root = Path(project_root)
    input_dir = root / "input"
    output_dir = root / "output"

    case_paths = sorted(input_dir.glob("EC_*.json"))
    if only_case:
        case_paths = [p for p in case_paths if p.stem == only_case]
        if not case_paths:
            raise SystemExit(f"Không tìm thấy input/{only_case}.json")

    started = time.perf_counter()
    print(f"Loading CSV từ {root / 'data'} ...")
    store = DataStore(root / "data")
    llm = LLMClient(project_root=root, enabled=use_llm)
    if not llm.enabled:
        print("! LLM degraded (thiếu GROQ_API_KEY hoặc --no-llm): agent chạy deterministic-only.")

    # Chỉ truncate trace khi chạy full batch — chạy 1 case là để debug, không
    # nên xoá trace của lượt chạy 50 case trước đó.
    trace = TraceWriter(root / "trace.jsonl", truncate=only_case is None)
    coordinator = Coordinator(store, llm, trace)

    issue_counter: Counter[str] = Counter()
    fallback_count = 0

    for path in case_paths:
        case_input = load_case(path)
        output = run_case(coordinator, case_input)
        write_output(output, output_dir / path.name)

        issue_counter[output.assessment.primary_issue] += 1
        if output.assessment.confidence <= 0.2:
            fallback_count += 1
        print(
            f"{output.case_id}  {output.assessment.primary_issue:<26}"
            f"refund={output.financial_resolution.recommended_refund_brl:>9.2f}  "
            f"conf={output.assessment.confidence:.2f}"
        )

    elapsed = round(time.perf_counter() - started, 2)

    if only_case is None:
        _write_metadata(root, llm, len(case_paths), elapsed)

    print("\n--- tổng kết ---")
    for issue, count in issue_counter.most_common():
        print(f"{issue:<26} {count}")
    print(f"cases={len(case_paths)}  trace_lines={trace.line_count}  elapsed={elapsed}s")
    print(f"llm_calls={llm.call_count}  llm_failures={llm.failure_count}  fallback={fallback_count}")

    return {"cases": len(case_paths), "trace_lines": trace.line_count}


def _write_metadata(root: Path, llm: LLMClient, case_count: int, elapsed: float) -> None:
    agent_names = [
        "coordinator",
        "order_seller_agent",
        "delivery_agent",
        "payment_agent",
        "policy_agent",
        "verifier_agent",
    ]
    metadata = {
        "model": llm_module.MODEL_NAME,
        "parameter_size": llm_module.MODEL_PARAM_SIZE,
        "provider": llm_module.PROVIDER,
        "framework": "custom in-process multi-agent (A2A-style handoff)",
        "runtime": {
            "language": "python",
            "python_version": platform.python_version(),
            "os": f"{platform.system()} {platform.release()}",
            "elapsed_seconds": elapsed,
            "cases_processed": case_count,
            "llm_calls": llm.call_count,
            "llm_failures": llm.failure_count,
            "llm_enabled": llm.enabled,
        },
        "agents": [
            {"name": name, "model": llm_module.MODEL_NAME,
             "parameter_size": llm_module.MODEL_PARAM_SIZE}
            for name in agent_names
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (root / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
