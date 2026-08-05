"""Client LLM dùng chung, OpenAI-compatible (Groq / OpenRouter).

Mọi lời gọi đều ghi trace để `logging/trace.jsonl` phản ánh đúng lượt chạy thật.
"""

from __future__ import annotations

import json
import time
from functools import lru_cache
from typing import Any

from openai import OpenAI

from .config import CONFIG, RunConfig
from .tracing import trace_event


_LLM_ENABLED = True


def set_llm_enabled(value: bool) -> None:
    """Tắt LLM cho smoke test offline rule engine (`--no-llm`). Mặc định bật."""
    global _LLM_ENABLED
    _LLM_ENABLED = value


def llm_enabled() -> bool:
    return _LLM_ENABLED


@lru_cache(maxsize=4)
def _client(provider: str) -> OpenAI:
    cfg = RunConfig(provider=provider)
    return OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=cfg.request_timeout_s)


def call_json(
    *,
    agent: str,
    case_id: str,
    system: str,
    user: str,
    cfg: RunConfig = CONFIG,
) -> dict[str, Any]:
    """Gọi model, ép trả JSON object. Raise sau khi hết retry."""
    client = _client(cfg.provider)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_err: Exception | None = None

    for attempt in range(1, cfg.max_retries + 1):
        started = time.perf_counter()
        try:
            resp = client.chat.completions.create(
                model=cfg.model.name,
                messages=messages,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or "{}"
            parsed = json.loads(content)
            trace_event(
                case_id=case_id,
                agent=agent,
                event="llm_call",
                payload={
                    "model": cfg.model.name,
                    "attempt": attempt,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
                    "completion_tokens": getattr(resp.usage, "completion_tokens", None),
                    "response": parsed,
                },
            )
            return parsed
        except Exception as err:  # noqa: BLE001 - retry mọi lỗi transient
            last_err = err
            trace_event(
                case_id=case_id,
                agent=agent,
                event="llm_error",
                payload={"attempt": attempt, "error": f"{type(err).__name__}: {err}"},
            )
            time.sleep(min(2**attempt, 8))

    raise RuntimeError(f"[{agent}] LLM thất bại sau {cfg.max_retries} lần") from last_err
