"""Order & Seller Agent.

Owns orders.csv, order_items.csv, sellers.csv. Given an order_id, reports
status, items, sellers and which seller (if any) handed off to the
carrier after their shipping_limit_date.
"""

from __future__ import annotations

from langchain.agents import create_agent

from src.a2a_protocol import A2AMessage
from src.llm import build_chat_model
from src.order_and_seller_agent.tools.order_tools import ORDER_SELLER_TOOLS
from src.schemas import OrderSellerReport
from src.tracer import TraceCallbackHandler, tracer

AGENT_NAME = "order_and_seller_agent"

SYSTEM_PROMPT = """You are the Order & Seller Agent in an e-commerce dispute pipeline.
You only know about orders, order items and sellers.

Steps:
1. Call lookup_order with the given order_id.
2. Call lookup_order_items_and_sellers with the same order_id.
3. Report back using ONLY values returned by the tools. Never invent or
   recompute a number, id, or date yourself.

item_ids must be formatted "<order_id>:<order_item_id>".
evidence_ids must only contain: order:<order_id>, item:<order_id>:<order_item_id>
(one per item actually returned by the tool), seller:<seller_id> (one per
seller_id returned by the tool). If order_found is false, return empty lists
and order_found=false, order_status="".
"""

_agent = create_agent(
    model=build_chat_model(),
    tools=ORDER_SELLER_TOOLS,
    system_prompt=SYSTEM_PROMPT,
    response_format=OrderSellerReport,
)


def run(message: A2AMessage) -> A2AMessage:
    tracer.log_a2a("receive", AGENT_NAME, message)

    order_id = message.data["order_id"]
    result = _agent.invoke(
        {"messages": [("user", f"Investigate order_id={order_id}")]},
        config={"callbacks": [TraceCallbackHandler(AGENT_NAME)]},
    )
    report: OrderSellerReport = result["structured_response"]

    response = A2AMessage(
        task_id=message.task_id,
        from_agent=AGENT_NAME,
        to_agent=message.from_agent,
        role="response",
        data=report.model_dump(),
        evidence_ids=report.evidence_ids,
    )
    tracer.log_a2a("send", AGENT_NAME, response)
    return response
