from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TraceWriter:
    def __init__(self, path: Path, run_id: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.run_id = run_id
        self.sequence = 0
        self._handle = path.open("w", encoding="utf-8", newline="\n")

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "TraceWriter":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def write(
        self,
        *,
        case_id: str,
        agent: str,
        event: str,
        input_from: str | None,
        payload: dict[str, Any],
    ) -> None:
        self.sequence += 1
        record = {
            "run_id": self.run_id,
            "sequence": self.sequence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "case_id": case_id,
            "agent": agent,
            "event": event,
            "input_from": input_from,
            "payload": payload,
        }
        self._handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        self._handle.write("\n")
        self._handle.flush()
