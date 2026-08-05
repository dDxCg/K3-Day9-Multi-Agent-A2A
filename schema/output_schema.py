"""Output schema theo README mục 6, kèm các giới hạn độ dài mảng.

Pydantic là chốt chặn cuối: nếu draft vi phạm giới hạn (>5 entity, >10 evidence,
confidence ngoài [0,1]...) thì ValidationError nổ trước khi kịp ghi file.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CaseStatus = Literal["action_required", "no_action"]

MAX_ENTITY_IDS = 5
MAX_EVIDENCE = 10
MAX_ROOT_CAUSES = 3
MAX_RESPONSIBLE_PARTIES = 3
MAX_ACTIONS = 5


class Assessment(BaseModel):
    primary_issue: str
    case_status: CaseStatus
    confidence: float = Field(ge=0.0, le=1.0)


class AffectedEntities(BaseModel):
    order_ids: list[str] = Field(default_factory=list, max_length=MAX_ENTITY_IDS)
    item_ids: list[str] = Field(default_factory=list, max_length=MAX_ENTITY_IDS)
    seller_ids: list[str] = Field(default_factory=list, max_length=MAX_ENTITY_IDS)
    payment_ids: list[str] = Field(default_factory=list, max_length=MAX_ENTITY_IDS)


class RankedCause(BaseModel):
    cause_code: str
    rank: int


class ResponsibleParty(BaseModel):
    party_type: str
    party_id: str


class RootCauseAnalysis(BaseModel):
    ranked_causes: list[RankedCause] = Field(
        default_factory=list, max_length=MAX_ROOT_CAUSES
    )
    responsible_parties: list[ResponsibleParty] = Field(
        default_factory=list, max_length=MAX_RESPONSIBLE_PARTIES
    )


class FinancialResolution(BaseModel):
    currency: str = "BRL"
    item_total_brl: float
    freight_total_brl: float
    payment_total_brl: float
    recommended_refund_brl: float


class CaseOutput(BaseModel):
    case_id: str
    assessment: Assessment
    affected_entities: AffectedEntities
    root_cause_analysis: RootCauseAnalysis
    evidence_ids: list[str] = Field(default_factory=list, max_length=MAX_EVIDENCE)
    financial_resolution: FinancialResolution
    resolution_actions: list[str] = Field(default_factory=list, max_length=MAX_ACTIONS)
