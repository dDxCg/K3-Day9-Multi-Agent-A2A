"""Switch output/ between evidence modes without re-running the pipeline.

Evidence is built deterministically by the verifier from the CSVs plus the
policy decision, and both are already recorded in every output file — so
flipping between FULL and CAUSAL costs no LLM call and no API credit.

The rewrite happens in place and is reversible at zero cost: run it again
with the other mode to get the previous evidence sets back.

    uv run python scripts/rebuild_evidence.py --mode CAUSAL
    uv run python scripts/rebuild_evidence.py --mode FULL
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import EVIDENCE_PROFILES
from src.verifier_agent.evidence import build_evidence, collect_facts

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--mode", choices=sorted(EVIDENCE_PROFILES), default="CAUSAL")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    mode = parse_args(argv).mode

    changed = 0
    removed_kinds: dict[str, int] = {}
    sizes: list[int] = []

    for path in sorted(OUTPUT_DIR.glob("EC_*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        order_ids = case["affected_entities"]["order_ids"]
        order_id = order_ids[0] if order_ids else ""
        decision = {
            "primary_issue": case["assessment"]["primary_issue"],
            "cause_code": case["root_cause_analysis"]["ranked_causes"][0]["cause_code"],
            "responsible_parties": case["root_cause_analysis"]["responsible_parties"],
        }
        facts = collect_facts(order_id, decision)
        new_evidence = build_evidence(order_id, decision, facts, mode)

        dropped = [e for e in case["evidence_ids"] if e not in new_evidence]
        if dropped:
            changed += 1
            for evidence_id in dropped:
                kind = evidence_id.split(":")[0]
                removed_kinds[kind] = removed_kinds.get(kind, 0) + 1

        case["evidence_ids"] = new_evidence
        sizes.append(len(new_evidence))
        path.write_text(
            json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"mode={mode} -> {OUTPUT_DIR} ({len(sizes)} cases)")
    print(f"cases with evidence removed: {changed}/{len(sizes)}")
    print(f"removed by kind: {removed_kinds or 'none'}")
    print(
        f"evidence per case: min={min(sizes)} max={max(sizes)} "
        f"avg={sum(sizes) / len(sizes):.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
