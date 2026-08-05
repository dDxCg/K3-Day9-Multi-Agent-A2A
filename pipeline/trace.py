"""Ghi trace.jsonl — mỗi handoff giữa hai agent là một dòng.

README mục 8: chỉ giữ lượt chạy mới nhất, không append qua nhiều lần chạy →
file bị truncate khi bắt đầu batch.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TraceWriter:
    def __init__(self, path: str | Path, truncate: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if truncate:
            self.path.write_text("", encoding="utf-8")
        self.line_count = 0

    def log(
        self,
        case_id: str,
        sender: str,
        receiver: str,
        message_type: str,
        payload: dict[str, Any],
    ) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "case_id": case_id,
            "from": sender,
            "to": receiver,
            "type": message_type,
            "payload": payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self.line_count += 1
