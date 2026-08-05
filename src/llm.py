"""Per-agent chat model factory, routed through OpenRouter."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from src.config import Config


def build_chat_model(temperature: float = 0.0) -> ChatOpenAI:
    return ChatOpenAI(
        model=Config.MODEL,
        api_key=Config.OPEN_ROUTER_API_KEY,
        base_url=Config.OPEN_ROUTER_BASE_URL,
        temperature=temperature,
    )
