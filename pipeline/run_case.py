"""Chạy một case: đọc input JSON → Coordinator → CaseOutput."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.coordinator import Coordinator
from schema.output_schema import CaseOutput


def load_case(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_case(coordinator: Coordinator, case_input: dict[str, Any]) -> CaseOutput:
    return coordinator.handle(case_input)


def write_output(output: CaseOutput, out_path: str | Path) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(output.model_dump(), ensure_ascii=False, indent=2)
    path.write_text(payload + "\n", encoding="utf-8")
