"""Base cho mọi agent: quyền truy cập dữ liệu, LLM và trace."""

from __future__ import annotations

from typing import Any

from data_access.data_store import DataStore
from llm.client import LLMClient
from pipeline.trace import TraceWriter


class BaseAgent:
    name = "agent"

    def __init__(self, store: DataStore, llm: LLMClient, trace: TraceWriter):
        self.store = store
        self.llm = llm
        self.trace = trace

    # -------------------------------------------------------------- LLM utils

    def ask(self, system: str, user: str, max_tokens: int = 96) -> dict[str, Any]:
        """Gọi LLM, luôn trả dict (rỗng nếu degraded) để caller không phải None-check."""
        result = self.llm.chat_json(system, user, max_tokens=max_tokens)
        return result or {}

    @staticmethod
    def clamp_confidence(raw: Any, fallback: float) -> float:
        """LLM chỉ được nhích confidence trong [0,1]; giá trị lạ thì dùng fallback."""
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return fallback
        if not 0.0 <= value <= 1.0:
            return fallback
        return round(value, 2)

    @staticmethod
    def lower_confidence(current: float, factor: float) -> float:
        """Hạ confidence — một chiều.

        Confidence deterministic được neo vào rule đã khớp và đã có cross-check
        độc lập; để một model 8B nâng nó lên chỉ thêm phương sai chứ không thêm
        thông tin. Model chỉ được phép bày tỏ sự nghi ngờ, không được tự tin hộ.
        """
        return round(min(max(current * factor, 0.0), 1.0), 2)

    # ------------------------------------------------------------ trace utils

    def emit(self, case_id: str, receiver: str, message_type: str, payload: dict[str, Any]) -> None:
        self.trace.log(case_id, self.name, receiver, message_type, payload)
