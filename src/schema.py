"""Schema output + hằng số nghiệp vụ theo README mục 4-6."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

PrimaryIssue = Literal[
    "canceled_order_paid",
    "unavailable_order_paid",
    "late_delivery_seller",
    "late_delivery_logistics",
    "valid_split_payment",
    "unsupported_late_claim",
]

RootCauseCode = Literal[
    "SELLER_HANDOFF_AFTER_LIMIT",
    "CARRIER_DELIVERED_AFTER_ESTIMATE",
    "ORDER_CANCELED_AFTER_PAYMENT",
    "ORDER_UNAVAILABLE_AFTER_PAYMENT",
    "MULTIPLE_PAYMENTS_RECONCILED",
    "DELIVERY_WITHIN_ESTIMATE",
]

ResolutionAction = Literal[
    "issue_full_refund",
    "refund_freight",
    "explain_valid_split_payment",
    "reject_late_refund",
]

PartyType = Literal["platform", "seller", "logistics_provider"]
CaseStatus = Literal["action_required", "no_action"]

# Bảng quy tắc README mục 4, xếp theo thứ tự ưu tiên đánh giá.
ISSUE_PRIORITY: tuple[PrimaryIssue, ...] = (
    "canceled_order_paid",
    "unavailable_order_paid",
    "late_delivery_seller",
    "late_delivery_logistics",
    "valid_split_payment",
    "unsupported_late_claim",
)

ISSUE_ACTION: dict[PrimaryIssue, ResolutionAction] = {
    "canceled_order_paid": "issue_full_refund",
    "unavailable_order_paid": "issue_full_refund",
    "late_delivery_seller": "refund_freight",
    "late_delivery_logistics": "refund_freight",
    "valid_split_payment": "explain_valid_split_payment",
    "unsupported_late_claim": "reject_late_refund",
}

ISSUE_ROOT_CAUSE: dict[PrimaryIssue, RootCauseCode] = {
    "canceled_order_paid": "ORDER_CANCELED_AFTER_PAYMENT",
    "unavailable_order_paid": "ORDER_UNAVAILABLE_AFTER_PAYMENT",
    "late_delivery_seller": "SELLER_HANDOFF_AFTER_LIMIT",
    "late_delivery_logistics": "CARRIER_DELIVERED_AFTER_ESTIMATE",
    "valid_split_payment": "MULTIPLE_PAYMENTS_RECONCILED",
    "unsupported_late_claim": "DELIVERY_WITHIN_ESTIMATE",
}

# Sai số đối soát payment vs (item + freight), README mục 4.
PAYMENT_TOLERANCE_BRL = 0.10

MAX_ENTITY_IDS = 5
MAX_EVIDENCE = 10
MAX_ROOT_CAUSES = 3
MAX_RESPONSIBLE_PARTIES = 3
MAX_ACTIONS = 5


class Assessment(BaseModel):
    primary_issue: PrimaryIssue
    case_status: CaseStatus
    confidence: float = Field(ge=0.0, le=1.0)


class AffectedEntities(BaseModel):
    order_ids: list[str] = Field(default_factory=list, max_length=MAX_ENTITY_IDS)
    item_ids: list[str] = Field(default_factory=list, max_length=MAX_ENTITY_IDS)
    seller_ids: list[str] = Field(default_factory=list, max_length=MAX_ENTITY_IDS)
    payment_ids: list[str] = Field(default_factory=list, max_length=MAX_ENTITY_IDS)


class RankedCause(BaseModel):
    cause_code: RootCauseCode
    rank: int = Field(ge=1)


class ResponsibleParty(BaseModel):
    party_type: PartyType
    party_id: str


class RootCauseAnalysis(BaseModel):
    ranked_causes: list[RankedCause] = Field(max_length=MAX_ROOT_CAUSES)
    responsible_parties: list[ResponsibleParty] = Field(
        default_factory=list, max_length=MAX_RESPONSIBLE_PARTIES
    )


class FinancialResolution(BaseModel):
    currency: Literal["BRL"] = "BRL"
    item_total_brl: float
    freight_total_brl: float
    payment_total_brl: float
    recommended_refund_brl: float

    @field_validator("*", mode="before")
    @classmethod
    def _round2(cls, v: object) -> object:
        return round(v, 2) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


class CaseOutput(BaseModel):
    case_id: str
    assessment: Assessment
    affected_entities: AffectedEntities
    root_cause_analysis: RootCauseAnalysis
    evidence_ids: list[str] = Field(max_length=MAX_EVIDENCE)
    financial_resolution: FinancialResolution
    resolution_actions: list[ResolutionAction] = Field(max_length=MAX_ACTIONS)


class CustomerRequest(BaseModel):
    language: str
    message: str
    claimed_order_id: str


class CaseInput(BaseModel):
    case_id: str
    opened_at: str
    customer_request: CustomerRequest
    policy_version: str


# --- Evidence ID builders (README mục 5) --------------------------------------


def ev_order(order_id: str) -> str:
    return f"order:{order_id}"


def ev_item(order_id: str, order_item_id: int | str) -> str:
    return f"item:{order_id}:{order_item_id}"


def ev_payment(order_id: str, payment_sequential: int | str) -> str:
    return f"payment:{order_id}:{payment_sequential}"


def ev_seller(seller_id: str) -> str:
    return f"seller:{seller_id}"


def ev_policy(root_cause_code: str) -> str:
    return f"policy:{root_cause_code}"
