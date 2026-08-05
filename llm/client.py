"""Wrapper LLM cho các agent.

Model name hard-code tại đây (README mục 9: không giấu tên model trong .env).
Chỉ API key đọc từ .env.

Ràng buộc đề bài: mỗi agent dùng model ≤ 10B tham số.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- model config

PROVIDER = "groq"
MODEL_NAME = "llama-3.1-8b-instant"
MODEL_PARAM_SIZE = "8B"
API_URL = "https://api.groq.com/openai/v1/chat/completions"
API_KEY_ENV = "GROQ_API_KEY"

DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 512
REQUEST_TIMEOUT_S = 45


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class LLMClient:
    """Client tối giản, không phụ thuộc SDK ngoài.

    Nếu thiếu API key hoặc call lỗi, client trả None và agent chạy ở chế độ
    degraded (kết luận vẫn đúng vì mọi con số do tầng deterministic quyết định).
    """

    def __init__(self, project_root: str | Path | None = None, enabled: bool = True):
        root = Path(project_root) if project_root else Path(__file__).resolve().parent.parent
        _load_dotenv(root / ".env")
        self.api_key = os.environ.get(API_KEY_ENV, "").strip()
        self.enabled = enabled and bool(self.api_key)
        self.call_count = 0
        self.failure_count = 0

    @property
    def model_info(self) -> dict[str, str]:
        return {
            "provider": PROVIDER,
            "model": MODEL_NAME,
            "parameter_size": MODEL_PARAM_SIZE,
        }

    def chat(self, system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str | None:
        if not self.enabled:
            return None

        payload = json.dumps(
            {
                "model": MODEL_NAME,
                "temperature": DEFAULT_TEMPERATURE,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        ).encode("utf-8")

        request = urllib.request.Request(
            API_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        self.call_count += 1
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                body = json.loads(response.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
        except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError, TimeoutError):
            self.failure_count += 1
            return None

    def chat_json(self, system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> dict[str, Any] | None:
        """Gọi model và parse JSON object đầu tiên trong câu trả lời."""
        raw = self.chat(system, user, max_tokens=max_tokens)
        if not raw:
            return None
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
