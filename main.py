import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.config import Config
from src.coordinator_agent.graph import run_case
from src.tracer import tracer

ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
METADATA_PATH = ROOT / "logging" / "metadata.json"


def main() -> None:
    tracer.start_run()
    started_at = datetime.now(timezone.utc).isoformat()

    OUTPUT_DIR.mkdir(exist_ok=True)
    input_files = sorted(INPUT_DIR.glob("EC_*.json"))

    processed = []
    for input_path in input_files:
        case_input = json.loads(input_path.read_text(encoding="utf-8"))
        case_output = run_case(case_input)
        output_path = OUTPUT_DIR / input_path.name
        output_path.write_text(
            json.dumps(case_output, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        processed.append(case_input["case_id"])
        print(f"{input_path.name} -> {output_path.name}")

    metadata = {
        "model": Config.MODEL,
        "provider": "openrouter",
        "base_url": Config.OPEN_ROUTER_BASE_URL,
        "framework": ["langchain", "langgraph"],
        "runtime": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "agents": [
            "coordinator_agent",
            "order_and_seller_agent",
            "delivery_agent",
            "payment_agent",
            "policy_agent (deterministic, no LLM)",
            "verifier_agent (deterministic, no LLM)",
        ],
        "cases_processed": len(processed),
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "token_usage_by_agent": tracer.token_usage,
    }
    METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    METADATA_PATH.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n{len(processed)} cases written to {OUTPUT_DIR}")
    print(f"trace: {tracer.path}")
    print(f"metadata: {METADATA_PATH}")


if __name__ == "__main__":
    main()
