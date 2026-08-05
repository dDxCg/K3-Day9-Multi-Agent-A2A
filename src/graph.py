"""Wiring LangGraph: coordinator fan-out 3 domain agent -> policy -> verifier -> finalize.

    coordinator ──┬─> order_seller ─┐
                  ├─> payment ──────┼─> policy -> verifier -> finalize -> END
                  └─> delivery ─────┘

Ba domain agent chạy song song, ghi vào `findings` qua reducer merge_findings.
Policy chỉ đọc findings (không đọc lại CSV) — đây là điểm handoff chính.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from .agents import (
    coordinator_node,
    delivery_node,
    finalize_node,
    order_seller_node,
    payment_node,
    policy_node,
    verifier_node,
)
from .state import CaseState

DOMAIN_AGENTS = ("order_seller", "payment", "delivery")


def build_graph() -> StateGraph:
    g = StateGraph(CaseState)

    g.add_node("coordinator", coordinator_node)
    g.add_node("order_seller", order_seller_node)
    g.add_node("payment", payment_node)
    g.add_node("delivery", delivery_node)
    g.add_node("policy", policy_node)
    g.add_node("verifier", verifier_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "coordinator")
    for agent in DOMAIN_AGENTS:
        g.add_edge("coordinator", agent)
        g.add_edge(agent, "policy")
    g.add_edge("policy", "verifier")
    g.add_edge("verifier", "finalize")
    g.add_edge("finalize", END)

    return g


@lru_cache(maxsize=1)
def compiled_graph():
    return build_graph().compile()
