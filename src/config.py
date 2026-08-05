"""Cấu hình chạy hệ thống.

Tên model KHÔNG đặt trong .env (đề yêu cầu model name nằm trong source code để chấm).
.env chỉ chứa API key và provider đang dùng.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
INPUT_DIR = REPO_ROOT / "input"
OUTPUT_DIR = REPO_ROOT / "output"
LOGGING_DIR = REPO_ROOT / "logging"
TRACE_PATH = LOGGING_DIR / "trace.jsonl"  # 1 dòng / case
TRACE_EVENTS_PATH = LOGGING_DIR / "trace_events.jsonl"  # stream sự kiện thô
METADATA_PATH = LOGGING_DIR / "metadata.json"

POLICY_VERSION = "EC_POLICY_V1"

# --- Model declaration (ràng buộc đề: mỗi agent <= 10B params) -----------------

MAX_PARAM_SIZE_B = 10.0


@dataclass(frozen=True)
class ModelSpec:
    provider: str  # groq | openrouter
    name: str  # model id gửi lên API
    param_size_b: float  # số tham số, tính bằng tỷ

    def __post_init__(self) -> None:
        if self.param_size_b > MAX_PARAM_SIZE_B:
            raise ValueError(
                f"{self.name} có {self.param_size_b}B params, vượt trần {MAX_PARAM_SIZE_B}B"
            )


MODEL_CATALOG: dict[str, ModelSpec] = {
    "groq": ModelSpec("groq", "llama-3.1-8b-instant", 8.0),
    "openrouter": ModelSpec("openrouter", "meta-llama/llama-3.1-8b-instruct", 8.0),
}

BASE_URLS = {
    "groq": os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
    "openrouter": os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
}

API_KEY_ENV = {"groq": "GROQ_API_KEY", "openrouter": "OPENROUTER_API_KEY"}


@dataclass(frozen=True)
class RunConfig:
    provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "groq"))
    temperature: float = 0.0
    max_tokens: int = 1024
    request_timeout_s: float = 60.0
    max_retries: int = 3
    framework: str = "langgraph"

    @property
    def model(self) -> ModelSpec:
        if self.provider not in MODEL_CATALOG:
            raise ValueError(
                f"LLM_PROVIDER={self.provider!r} chưa khai báo trong MODEL_CATALOG"
            )
        return MODEL_CATALOG[self.provider]

    @property
    def base_url(self) -> str:
        return BASE_URLS[self.provider]

    @property
    def api_key(self) -> str:
        key = os.getenv(API_KEY_ENV[self.provider], "")
        if not key:
            raise RuntimeError(
                f"Thiếu {API_KEY_ENV[self.provider]} trong .env (copy từ .env.example)"
            )
        return key


CONFIG = RunConfig()
