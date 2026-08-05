"""Lightweight A2A-style message envelope for in-process agent-to-agent handoff.

Mirrors the shape of the Agent2Agent protocol (task id, role, structured
data part) without running separate HTTP servers per agent. Each agent
receives an A2AMessage and returns one, so handoff is always through this
contract instead of raw dict passing.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class A2AMessage(BaseModel):
    task_id: str
    from_agent: str
    to_agent: str
    role: Literal["request", "response"]
    data: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    notes: str = ""
