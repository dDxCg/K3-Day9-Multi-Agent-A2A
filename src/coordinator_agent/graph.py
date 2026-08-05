"""Coordinator Agent: a LangGraph pipeline that hands a case off between
the five other agents via A2AMessage and assembles/verifies the final
output. This is the only node that touches the input JSON and writes the
output JSON — every domain fact still comes from the specialist agents.
"""

from __future__ import annotations

import uuid

from langgraph.graph import END, START, StateGraph

from src.a2a_protocol import A2AMessage
from src.coordinator_agent.state import CaseState
from src.delivery_agent.agent import run as run_delivery
from src.order_and_seller_agent.agent import run as run_order_seller
from src.payment_agent import agent as payment_agent_module
from src.policy_agent.agent import run as run_policy
from src.schemas import (
    AffectedEntities,
    Assessment,
    CaseOutput,
    DeliveryReport,
    FinancialResolution,
    OrderSellerReport,
    PaymentReport,
    RankedCause,
    ResponsibleParty,
    RootCauseAnalysis,
)
from src.tracer import tracer
from src.verifier_agent.agent import verify_and_fix

COORDINATOR = "coordinator_agent"


def _order_seller_node(state: CaseState) -> dict:
    msg = A2AMessage(
        task_id=state["case_id"],
        from_agent=COORDINATOR,
        to_agent="order_and_seller_agent",
        role="request",
        data={"order_id": state["order_id"]},
    )
    response = run_order_seller(msg)
    return {"order_seller_report": response.data}


def _delivery_node(state: CaseState) -> dict:
    msg = A2AMessage(
        task_id=state["case_id"],
        from_agent=COORDINATOR,
        to_agent="delivery_agent",
        role="request",
        data={"order_id": state["order_id"]},
    )
    response = run_delivery(msg)
    return {"delivery_report": response.data}


def _payment_node(state: CaseState) -> dict:
    osr = state["order_seller_report"]
    msg = A2AMessage(
        task_id=state["case_id"],
        from_agent=COORDINATOR,
        to_agent="payment_agent",
        role="request",
        data={
            "order_id": state["order_id"],
            "item_total_brl": osr.get("item_total_brl", 0.0),
            "freight_total_brl": osr.get("freight_total_brl", 0.0),
        },
    )
    response = payment_agent_module.run(msg)
    return {"payment_report": response.data}


def _policy_node(state: CaseState) -> dict:
    response = run_policy(
        state["case_id"],
        OrderSellerReport(**state["order_seller_report"]),
        DeliveryReport(**state["delivery_report"]),
        PaymentReport(**state["payment_report"]),
        state.get("opened_at", ""),
    )
    return {"policy_decision": response.data}


def _assemble_node(state: CaseState) -> dict:
    osr = state["order_seller_report"]
    payment = state["payment_report"]
    decision = state["policy_decision"]

    # Provisional: the verifier rebuilds both entities and evidence from the
    # CSVs, so what the agents reported here is only a starting point.
    evidence_ids = list(
        dict.fromkeys(
            [f"policy:{decision['cause_code']}"]
            + osr.get("evidence_ids", [])
            + state["delivery_report"].get("evidence_ids", [])
            + payment.get("evidence_ids", [])
        )
    )

    case = CaseOutput(
        case_id=state["case_id"],
        assessment=Assessment(
            primary_issue=decision["primary_issue"],
            case_status=decision["case_status"],
            confidence=decision["confidence"],
        ),
        affected_entities=AffectedEntities(
            order_ids=[state["order_id"]],
            item_ids=osr.get("item_ids", []),
            seller_ids=osr.get("seller_ids", []),
            payment_ids=payment.get("payment_ids", []),
        ),
        root_cause_analysis=RootCauseAnalysis(
            ranked_causes=[RankedCause(cause_code=decision["cause_code"], rank=1)],
            responsible_parties=[
                ResponsibleParty(**p) for p in decision["responsible_parties"]
            ],
        ),
        evidence_ids=evidence_ids,
        financial_resolution=FinancialResolution(
            currency="BRL",
            item_total_brl=osr.get("item_total_brl", 0.0),
            freight_total_brl=osr.get("freight_total_brl", 0.0),
            payment_total_brl=payment.get("payment_total_brl", 0.0),
            recommended_refund_brl=decision["recommended_refund_brl"],
        ),
        resolution_actions=decision["resolution_actions"],
    )
    return {"case_output": case.model_dump()}


def _verifier_node(state: CaseState) -> dict:
    verified = verify_and_fix(
        CaseOutput(**state["case_output"]),
        state["order_id"],
        state["policy_decision"],
    )
    return {"case_output": verified.model_dump()}


def build_graph():
    graph = StateGraph(CaseState)
    graph.add_node("order_and_seller_agent", _order_seller_node)
    graph.add_node("delivery_agent", _delivery_node)
    graph.add_node("payment_agent", _payment_node)
    graph.add_node("policy_agent", _policy_node)
    graph.add_node("assemble", _assemble_node)
    graph.add_node("verifier_agent", _verifier_node)

    graph.add_edge(START, "order_and_seller_agent")
    graph.add_edge("order_and_seller_agent", "delivery_agent")
    graph.add_edge("delivery_agent", "payment_agent")
    graph.add_edge("payment_agent", "policy_agent")
    graph.add_edge("policy_agent", "assemble")
    graph.add_edge("assemble", "verifier_agent")
    graph.add_edge("verifier_agent", END)
    return graph.compile()


_COMPILED_GRAPH = None


def run_case(case_input: dict) -> dict:
    global _COMPILED_GRAPH
    if _COMPILED_GRAPH is None:
        _COMPILED_GRAPH = build_graph()
    graph = _COMPILED_GRAPH

    case_id = case_input["case_id"]
    tracer.set_case(case_id)
    tracer.log("case_start", case_id=case_id)

    state: CaseState = {
        "case_id": case_id,
        "order_id": case_input["customer_request"]["claimed_order_id"],
        "customer_message": case_input["customer_request"]["message"],
        "policy_version": case_input.get("policy_version", ""),
        "opened_at": case_input.get("opened_at", ""),
    }
    final_state = graph.invoke(state, config={"run_id": uuid.uuid4()})
    tracer.log("case_end", case_id=case_id)
    return final_state["case_output"]
