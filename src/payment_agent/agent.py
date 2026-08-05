"""Payment Agent: reconciles payments against item + freight totals."""

from __future__ import annotations

from langchain.agents import create_agent

from src.a2a_protocol import A2AMessage
from src.llm import build_chat_model
from src.payment_agent.tools import PAYMENT_TOOLS
from src.schemas import PaymentReport
from src.tracer import TraceCallbackHandler, tracer

AGENT_NAME = "payment_agent"

SYSTEM_PROMPT = """You are the Payment Agent in an e-commerce dispute pipeline.
You only know about payment rows for one order, and whether they
reconcile against the item + freight total you are given.

Steps:
1. Call lookup_payments with the given order_id.
2. Call reconcile_with_order_total with the order_id and the given
   item_total_brl and freight_total_brl.
3. Report back using ONLY values returned by the tools. Never invent or
   recompute a number yourself.

payment_ids must be formatted "<order_id>:<payment_sequential>", one per
payment row returned by lookup_payments.
evidence_ids must contain one payment:<order_id>:<payment_sequential> per
payment row.
"""

_agent = create_agent(
    model=build_chat_model(),
    tools=PAYMENT_TOOLS,
    system_prompt=SYSTEM_PROMPT,
    response_format=PaymentReport,
)


def run(message: A2AMessage) -> A2AMessage:
    tracer.log_a2a("receive", AGENT_NAME, message)

    order_id = message.data["order_id"]
    item_total = message.data.get("item_total_brl", 0.0)
    freight_total = message.data.get("freight_total_brl", 0.0)

    result = _agent.invoke(
        {
            "messages": [
                (
                    "user",
                    f"Investigate order_id={order_id}. "
                    f"item_total_brl={item_total} freight_total_brl={freight_total}",
                )
            ]
        },
        config={"callbacks": [TraceCallbackHandler(AGENT_NAME)]},
    )
    report: PaymentReport = result["structured_response"]

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
