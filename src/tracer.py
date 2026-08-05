"""JSONL run tracer + LangChain callback handler.

Every agent/tool call is logged automatically through this callback
handler (wired into each agent's model via `callbacks=[trace_handler]`)
instead of any agent manually appending trace lines itself. `trace.jsonl`
is truncated once per run, per README (latest run only, no append across runs).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler

TRACE_PATH = Path(__file__).resolve().parent.parent / "logging" / "trace.jsonl"

_lock = threading.Lock()


class Tracer:
    def __init__(self, path: Path = TRACE_PATH) -> None:
        self.path = path
        self._current_case_id: str | None = None
        self.token_usage: dict[str, dict[str, int]] = {}

    def start_run(self) -> None:
        with _lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")
        self.token_usage = {}

    def set_case(self, case_id: str) -> None:
        self._current_case_id = case_id

    def log(self, event: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "case_id": self._current_case_id,
            "event": event,
            **fields,
        }
        with _lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def add_tokens(self, agent: str, input_tokens: int, output_tokens: int) -> None:
        with _lock:
            entry = self.token_usage.setdefault(
                agent, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            )
            entry["input_tokens"] += input_tokens
            entry["output_tokens"] += output_tokens
            entry["total_tokens"] += input_tokens + output_tokens

    def log_a2a(self, direction: str, agent: str, message: Any) -> None:
        """direction: 'receive' (agent got a request) or 'send' (agent
        handed a response to another agent)."""
        self.log(
            f"a2a_{direction}",
            agent=agent,
            task_id=getattr(message, "task_id", None),
            from_agent=getattr(message, "from_agent", None),
            to_agent=getattr(message, "to_agent", None),
            role=getattr(message, "role", None),
            data=getattr(message, "data", None),
            evidence_ids=getattr(message, "evidence_ids", None),
        )


tracer = Tracer()


class TraceCallbackHandler(BaseCallbackHandler):
    """Logs every LLM call and tool call for one agent to trace.jsonl."""

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name

    def on_chat_model_start(
        self, serialized: dict, messages: list, *, run_id: UUID, **kwargs: Any
    ) -> None:
        tracer.log(
            "llm_start",
            agent=self.agent_name,
            run_id=str(run_id),
            messages=[[m.type, m.content] for batch in messages for m in batch],
        )

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        try:
            text = response.generations[0][0].text
        except Exception:
            text = str(response)

        input_tokens = output_tokens = 0
        try:
            usage = response.generations[0][0].message.usage_metadata
            if usage:
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
        except Exception:
            pass
        if not input_tokens and not output_tokens:
            usage = (response.llm_output or {}).get("token_usage") or {}
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)

        if input_tokens or output_tokens:
            tracer.add_tokens(self.agent_name, input_tokens, output_tokens)

        tracer.log(
            "llm_end",
            agent=self.agent_name,
            run_id=str(run_id),
            output=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def on_tool_start(
        self, serialized: dict, input_str: str, *, run_id: UUID, **kwargs: Any
    ) -> None:
        tracer.log(
            "tool_start",
            agent=self.agent_name,
            run_id=str(run_id),
            tool=serialized.get("name"),
            input=input_str,
        )

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        tracer.log(
            "tool_end", agent=self.agent_name, run_id=str(run_id), output=str(output)
        )

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        tracer.log(
            "tool_error", agent=self.agent_name, run_id=str(run_id), error=str(error)
        )
