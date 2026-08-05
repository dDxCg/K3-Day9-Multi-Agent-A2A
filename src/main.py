"""CLI chạy pipeline.

    py -3 -m src.main --case EC_001          # chạy 1 case, in ra stdout
    py -3 -m src.main --all                  # chạy 50 case, ghi output/ + trace + metadata
    py -3 -m src.main --all --limit 5        # smoke test
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import INPUT_DIR, OUTPUT_DIR
from .graph import compiled_graph
from .llm import set_llm_enabled
from .tracing import reset_trace, trace_case, write_metadata


def load_case(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_case(case: dict) -> dict:
    """Trả state cuối của graph (chứa draft + verification)."""
    return compiled_graph().invoke({"case": case})


def write_output(case_id: str, payload: dict) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{case_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Multi-agent e-commerce dispute resolution")
    parser.add_argument("--case", help="case_id đơn lẻ, ví dụ EC_001")
    parser.add_argument("--all", action="store_true", help="chạy toàn bộ input/")
    parser.add_argument("--limit", type=int, default=0, help="giới hạn số case khi --all")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="chỉ chạy rule engine tất định, bỏ lớp xác nhận bằng LLM (smoke test offline)",
    )
    args = parser.parse_args(argv)

    if args.no_llm:
        set_llm_enabled(False)

    if args.case:
        case = load_case(INPUT_DIR / f"{args.case}.json")
        print(json.dumps(run_case(case).get("draft") or {}, ensure_ascii=False, indent=2))
        return 0

    if not args.all:
        parser.print_help()
        return 2

    paths = sorted(INPUT_DIR.glob("EC_*.json"))
    if args.limit:
        paths = paths[: args.limit]

    reset_trace()
    failures: list[str] = []
    unverified: list[str] = []
    total_llm_calls = 0
    total_llm_skipped = 0
    for path in paths:
        case = load_case(path)
        case_id = case["case_id"]
        try:
            final = run_case(case)
            draft = final.get("draft") or {}
            problems = (final.get("verification") or {}).get("problems", [])
            ok = bool((final.get("verification") or {}).get("ok"))
            write_output(case_id, draft)
            record = trace_case(
                case_id=case_id,
                order_id=final.get("order_id", ""),
                output=draft,
                ok=ok,
            )
            total_llm_calls += record["n_llm_calls"]
            total_llm_skipped += sum(1 for s in record["steps"] if s["event"] == "llm_skipped")
            if ok:
                print(f"[ok] {case_id}")
            else:
                unverified.append(f"{case_id}: {', '.join(problems)}")
                print(f"[warn] {case_id} verifier: {', '.join(problems)}", file=sys.stderr)
        except Exception as err:  # noqa: BLE001
            failures.append(f"{case_id}: {type(err).__name__}: {err}")
            print(f"[FAIL] {case_id}: {err}", file=sys.stderr)

    written = len(paths) - len(failures)
    write_metadata(
        cases_run=written,
        notes="; ".join(failures + unverified) or "tất cả case qua verifier",
        llm_calls=total_llm_calls,
        llm_skipped=total_llm_skipped,
    )
    print(f"\n{written}/{len(paths)} case ghi ra {OUTPUT_DIR}")
    print(f"LLM: {total_llm_calls} call thành công, {total_llm_skipped} lần bị skip/lỗi.")
    if total_llm_calls == 0 and total_llm_skipped > 0:
        print(
            "[warn] LLM chưa từng gọi thành công lần nào — kiểm tra API key/provider trong .env.",
            file=sys.stderr,
        )
    if unverified:
        print(f"{len(unverified)} case ghi ra nhưng KHÔNG qua verifier:", file=sys.stderr)
        for line in unverified:
            print(f"  - {line}", file=sys.stderr)
    return 1 if failures or unverified else 0


if __name__ == "__main__":
    raise SystemExit(main())
