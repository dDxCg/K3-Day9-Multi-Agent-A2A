"""Ghi trace.jsonl — mỗi handoff giữa hai agent là một dòng.

README mục 8: chỉ giữ lượt chạy mới nhất, không append qua nhiều lần chạy →
file bị truncate khi bắt đầu batch.

Ghi song song ra nhiều đường dẫn (root theo yêu cầu README, và logging/ theo
quy ước sẵn có của nhóm) để không ai phải nhớ file thật nằm ở đâu.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class TraceWriter:
    def __init__(self, paths: str | Path | Iterable[str | Path], truncate: bool = True):
        if isinstance(paths, (str, Path)):
            paths = [paths]
        self.paths = [Path(p) for p in paths]
        for path in self.paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            if truncate:
                path.write_text("", encoding="utf-8")
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
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        for path in self.paths:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        self.line_count += 1
