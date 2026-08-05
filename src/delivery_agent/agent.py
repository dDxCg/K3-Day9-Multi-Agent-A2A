"""Delivery Agent: compares actual customer delivery time to the estimate."""

from __future__ import annotations

from langchain.agents import create_agent

from src.a2a_protocol import A2AMessage
from src.delivery_agent.tools import DELIVERY_TOOLS
from src.llm import build_chat_model
from src.schemas import DeliveryReport
from src.tracer import TraceCallbackHandler, tracer

AGENT_NAME = "delivery_agent"

SYSTEM_PROMPT = """You are the Delivery Agent in an e-commerce dispute pipeline.
You only know about delivery timing (actual vs estimated) for one order.

Steps:
1. Call lookup_delivery_timing with the given order_id.
2. Report back using ONLY values returned by the tool. Never invent or
   recompute a date or boolean yourself.

evidence_ids must contain exactly one entry: order:<order_id>.
If found is false, return delivered_late=false and empty evidence_ids.
"""

_agent = create_agent(
    model=build_chat_model(),
    tools=DELIVERY_TOOLS,
    system_prompt=SYSTEM_PROMPT,
    response_format=DeliveryReport,
)


def run(message: A2AMessage) -> A2AMessage:
    tracer.log_a2a("receive", AGENT_NAME, message)

    order_id = message.data["order_id"]
    result = _agent.invoke(
        {"messages": [("user", f"Investigate order_id={order_id}")]},
        config={"callbacks": [TraceCallbackHandler(AGENT_NAME)]},
    )
    report: DeliveryReport = result["structured_response"]

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
