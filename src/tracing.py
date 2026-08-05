"""Ghi trace.jsonl và metadata.json.

Đề yêu cầu trace chỉ chứa lượt chạy mới nhất -> `reset_trace()` gọi một lần
ở đầu mỗi lần chạy full 50 case, ghi đè cả hai file trace.

Hai mức trace:

- `logging/trace.jsonl`   — ĐÚNG 1 dòng cho mỗi case (50 dòng cho lượt chạy đầy đủ).
  Mỗi dòng gói trọn chuỗi handoff của case đó trong mảng `steps`, nên vẫn đọc được
  đầy đủ ai giao việc cho ai mà không phá ràng buộc 1 case = 1 dòng.
- `logging/trace_events.jsonl` — stream sự kiện thô, ghi ngay lúc chạy, dùng để debug.

metadata.json sinh hoàn toàn từ CONFIG (src/config.py) — không hardcode chuỗi model.
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from typing import Any

from .config import (
    API_KEY_ENV,
    CONFIG,
    LOGGING_DIR,
    METADATA_PATH,
    POLICY_VERSION,
    REPO_ROOT,
    TRACE_EVENTS_PATH,
    TRACE_PATH,
)

_RUN_ID: str | None = None
_CASE_STEPS: dict[str, list[dict[str, Any]]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_id() -> str | None:
    return _RUN_ID


def reset_trace(run_id: str | None = None) -> str:
    """Xoá trace cũ, mở lượt chạy mới. Trả run_id."""
    global _RUN_ID
    LOGGING_DIR.mkdir(parents=True, exist_ok=True)
    _RUN_ID = run_id or datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
    _CASE_STEPS.clear()
    TRACE_PATH.write_text("", encoding="utf-8")
    TRACE_EVENTS_PATH.write_text("", encoding="utf-8")
    return _RUN_ID


def trace_event(*, case_id: str, agent: str, event: str, payload: dict[str, Any]) -> None:
    """Ghi một bước của agent: vào stream thô, đồng thời gom vào bản tóm tắt của case."""
    LOGGING_DIR.mkdir(parents=True, exist_ok=True)
    step = {"ts": _now(), "agent": agent, "event": event, "payload": payload}
    _CASE_STEPS.setdefault(case_id, []).append(step)

    record = {"run_id": _RUN_ID, "case_id": case_id, **step}
    with TRACE_EVENTS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def trace_case(*, case_id: str, order_id: str, output: dict[str, Any], ok: bool) -> dict[str, Any]:
    """Chốt case: ghi ĐÚNG một dòng vào trace.jsonl, gồm cả chuỗi handoff của case.

    Trả về record đã ghi để caller (CLI) đếm được llm_call/llm_skipped toàn run
    mà không phải đọc lại file.
    """
    LOGGING_DIR.mkdir(parents=True, exist_ok=True)
    steps = _CASE_STEPS.pop(case_id, [])
    assessment = output.get("assessment", {})
    financial = output.get("financial_resolution", {})

    record = {
        "run_id": _RUN_ID,
        "ts": _now(),
        "case_id": case_id,
        "order_id": order_id,
        "ok": ok,
        "model": CONFIG.model.name,
        "framework": CONFIG.framework,
        "policy_version": POLICY_VERSION,
        "agent_path": [s["agent"] for s in steps],
        "n_llm_calls": sum(1 for s in steps if s["event"] == "llm_call"),
        "result": {
            "primary_issue": assessment.get("primary_issue"),
            "case_status": assessment.get("case_status"),
            "confidence": assessment.get("confidence"),
            "recommended_refund_brl": financial.get("recommended_refund_brl"),
            "resolution_actions": output.get("resolution_actions"),
            "n_evidence": len(output.get("evidence_ids", [])),
        },
        "steps": steps,
    }
    with TRACE_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def write_metadata(
    *,
    cases_run: int,
    notes: str = "",
    llm_calls: int = 0,
    llm_skipped: int = 0,
) -> None:
    """Sinh metadata.json từ CONFIG. Model name lấy từ MODEL_CATALOG, không gõ tay."""
    model = CONFIG.model
    METADATA_PATH.write_text(
        json.dumps(
            {
                "run_id": _RUN_ID,
                "generated_at": _now(),
                "model": {
                    "name": model.name,
                    "provider": model.provider,
                    "param_size_b": model.param_size_b,
                    "param_size": f"{model.param_size_b}B",
                    "declared_in": "src/config.py:MODEL_CATALOG",
                    "api_key_env": API_KEY_ENV[model.provider],
                },
                "framework": CONFIG.framework,
                "runtime": {
                    "language": "python",
                    "version": sys.version.split()[0],
                    "implementation": platform.python_implementation(),
                    "platform": platform.platform(),
                    "os": platform.system(),
                },
                "policy_version": POLICY_VERSION,
                "sampling": {
                    "temperature": CONFIG.temperature,
                    "max_tokens": CONFIG.max_tokens,
                },
                "cases_run": cases_run,
                "llm_health": {
                    "n_llm_calls": llm_calls,
                    "n_llm_skipped": llm_skipped,
                    "skip_rate": round(llm_skipped / (llm_calls + llm_skipped), 3)
                    if (llm_calls + llm_skipped)
                    else 0.0,
                },
                "notes": notes,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    mirror_to_repo_root()


def mirror_to_repo_root() -> None:
    """README mục 8 yêu cầu trace.jsonl và metadata.json nằm 'trong repo'.

    Bản gốc vẫn ở logging/; đây là bản sao ở root cho khớp chữ trong đề.
    """
    for source in (TRACE_PATH, METADATA_PATH):
        if source.exists():
            (REPO_ROOT / source.name).write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8"
            )
