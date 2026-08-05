"""Shared structured-output contracts.

The three data agents (Order & Seller, Delivery, Payment) each return one
of the `*Report` models below as their A2AMessage.data payload — this is
what forces every number/evidence-id to come from a tool call instead of
free-form LLM text. Policy and Verifier consume these reports plus the
final `CaseOutput`, which mirrors the README's output schema exactly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OrderSellerReport(BaseModel):
    order_id: str
    order_found: bool
    order_status: str = ""
    item_ids: list[str] = Field(default_factory=list)
    seller_ids: list[str] = Field(default_factory=list)
    late_seller_ids: list[str] = Field(
        default_factory=list,
        description="Sellers whose order_delivered_carrier_date is after that item's shipping_limit_date",
    )
    item_total_brl: float = 0.0
    freight_total_brl: float = 0.0
    order_delivered_carrier_date: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    notes: str = ""


class DeliveryReport(BaseModel):
    order_id: str
    delivered_late: bool = False
    order_delivered_customer_date: str | None = None
    order_estimated_delivery_date: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    notes: str = ""


class PaymentReport(BaseModel):
    order_id: str
    payment_ids: list[str] = Field(default_factory=list)
    payment_total_brl: float = 0.0
    payment_count: int = 0
    matches_item_freight: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
    notes: str = ""


class RankedCause(BaseModel):
    cause_code: str
    rank: int


class ResponsibleParty(BaseModel):
    party_type: str
    party_id: str


class Assessment(BaseModel):
    primary_issue: str
    case_status: str
    confidence: float


class AffectedEntities(BaseModel):
    order_ids: list[str] = Field(default_factory=list)
    item_ids: list[str] = Field(default_factory=list)
    seller_ids: list[str] = Field(default_factory=list)
    payment_ids: list[str] = Field(default_factory=list)


class RootCauseAnalysis(BaseModel):
    ranked_causes: list[RankedCause] = Field(default_factory=list)
    responsible_parties: list[ResponsibleParty] = Field(default_factory=list)


class FinancialResolution(BaseModel):
    currency: str = "BRL"
    item_total_brl: float = 0.0
    freight_total_brl: float = 0.0
    payment_total_brl: float = 0.0
    recommended_refund_brl: float = 0.0


class CaseOutput(BaseModel):
    case_id: str
    assessment: Assessment
    affected_entities: AffectedEntities
    root_cause_analysis: RootCauseAnalysis
    evidence_ids: list[str] = Field(default_factory=list)
    financial_resolution: FinancialResolution
    resolution_actions: list[str] = Field(default_factory=list)
