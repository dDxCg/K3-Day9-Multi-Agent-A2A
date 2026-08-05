"""Wrapper LLM cho các agent — gọi OpenAI API.

Model name hard-code tại đây (README mục 9: không giấu tên model trong .env).
Chỉ API key đọc từ .env.

LƯU Ý VỀ RÀNG BUỘC ≤10B THAM SỐ:
OpenAI không công bố số tham số của model nào, nên `MODEL_PARAM_SIZE` ghi
"undisclosed" thay vì một con số bịa ra. Nếu ràng buộc ≤10B được chấm chặt,
cần đổi sang model có số tham số công khai (ví dụ chạy local Qwen2.5-7B hoặc
Llama-3.1-8B).
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

PROVIDER = "openai"
MODEL_NAME = "gpt-4o-mini"
MODEL_PARAM_SIZE = "undisclosed"  # OpenAI không công bố
API_URL = "https://api.openai.com/v1/chat/completions"
API_KEY_ENV = "OPENAI_API_KEY"

DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 256
REQUEST_TIMEOUT_S = 45
MAX_RETRIES = 2


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
    """Client tối giản, không phụ thuộc SDK ngoài (chỉ dùng urllib).

    Nếu thiếu API key hoặc call lỗi, client trả None và agent chạy ở chế độ
    degraded — kết luận không đổi vì mọi con số do tầng deterministic quyết định.
    """

    def __init__(self, project_root: str | Path | None = None, enabled: bool = True):
        root = Path(project_root) if project_root else Path(__file__).resolve().parent.parent
        _load_dotenv(root / ".env")
        self.api_key = os.environ.get(API_KEY_ENV, "").strip()
        self.enabled = enabled and bool(self.api_key)
        self.call_count = 0
        self.failure_count = 0
        self.device = PROVIDER  # để metadata.json ghi thống nhất

    @property
    def model_info(self) -> dict[str, str]:
        return {
            "provider": PROVIDER,
            "model": MODEL_NAME,
            "parameter_size": MODEL_PARAM_SIZE,
        }

    def chat(
        self,
        system: str,
        user: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        json_mode: bool = True,
    ) -> str | None:
        if not self.enabled:
            return None

        body: dict[str, Any] = {
            "model": MODEL_NAME,
            "temperature": DEFAULT_TEMPERATURE,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # Ép model trả JSON hợp lệ ở mức API thay vì cầu may vào prompt.
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        payload = json.dumps(body).encode("utf-8")
        self.call_count += 1

        for attempt in range(MAX_RETRIES + 1):
            request = urllib.request.Request(
                API_URL,
                data=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                    parsed = json.loads(response.read().decode("utf-8"))
                return parsed["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as exc:
                # 429/5xx đáng thử lại; 4xx còn lại là lỗi cấu hình, thử lại vô ích.
                if exc.code not in (429, 500, 502, 503, 504) or attempt == MAX_RETRIES:
                    self.failure_count += 1
                    return None
            except (urllib.error.URLError, KeyError, IndexError,
                    json.JSONDecodeError, TimeoutError):
                if attempt == MAX_RETRIES:
                    self.failure_count += 1
                    return None
        self.failure_count += 1
        return None

    def chat_json(
        self, system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> dict[str, Any] | None:
        """Gọi model và parse JSON object trong câu trả lời."""
        raw = self.chat(system, user, max_tokens=max_tokens)
        if not raw:
            return None
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            self.failure_count += 1
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            self.failure_count += 1
            return None
        if not isinstance(parsed, dict):
            self.failure_count += 1
            return None
        return parsed
