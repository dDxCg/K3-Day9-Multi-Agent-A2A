from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL = "gemini-2.5-flash-lite"
DEFAULT_POLICY = "EC_POLICY_V1"


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE pairs without replacing process environment."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


@dataclass(frozen=True, slots=True)
class AppConfig:
    root: Path
    data_dir: Path
    input_dir: Path
    output_dir: Path
    trace_path: Path
    metadata_path: Path
    env_path: Path
    model: str
    google_api_key: str
    temperature: float
    top_p: float
    max_output_tokens: int
    request_timeout_seconds: int
    max_retries: int
    request_delay_seconds: float

    @classmethod
    def from_root(cls, root: Path) -> "AppConfig":
        root = root.resolve()
        env_path = root / ".env"
        load_env_file(env_path)
        return cls(
            root=root,
            data_dir=root / "data",
            input_dir=root / "input",
            output_dir=root / "output",
            trace_path=root / "logging" / "trace.jsonl",
            metadata_path=root / "logging" / "metadata.json",
            env_path=env_path,
            model=os.getenv("LLM_MODEL", DEFAULT_MODEL),
            google_api_key=os.getenv("GOOGLE_API_KEY", ""),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
            top_p=float(os.getenv("LLM_TOP_P", "0.95")),
            max_output_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2048")),
            request_timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "60")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
            request_delay_seconds=float(
                os.getenv("LLM_REQUEST_DELAY_SECONDS", "4")
            ),
        )

    def validate(self, with_llm: bool) -> None:
        required = [self.data_dir, self.input_dir]
        missing = [str(path) for path in required if not path.is_dir()]
        if missing:
            raise FileNotFoundError(f"Missing required directories: {missing}")
        if self.model != DEFAULT_MODEL:
            raise ValueError(
                f"Configured model {self.model!r} differs from source model "
                f"{DEFAULT_MODEL!r}. Update source and metadata together."
            )
        if with_llm and not self.google_api_key:
            raise ValueError("GOOGLE_API_KEY is required when --with-llm is used")
