from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class InvestigationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AlertEnvelope(BaseModel):
    source: str
    severity: str
    incident_key: str
    entity_ids: list[str] = Field(default_factory=list)
    timestamps: dict[str, datetime] = Field(default_factory=dict)
    raw_payload_ref: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class ServiceIdentity(BaseModel):
    canonical_service_id: str
    owner: str | None = None
    env: str
    dependency_graph_refs: list[str] = Field(default_factory=list)
    mapped_provider_ids: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    ambiguous_candidates: list[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    provider: str
    rationale: str
    timeout_seconds: int = Field(default=30, ge=1)
    budget_weight: int = Field(default=1, ge=1)
    capability: str


class InvestigationPlan(BaseModel):
    investigation_id: str
    ordered_steps: list[PlanStep] = Field(default_factory=list)
    max_api_calls: int = Field(default=20, ge=1)
    max_stage_wall_clock_seconds: int = Field(default=600, ge=30)


class EvidenceItem(BaseModel):
    provider: str
    timestamp: datetime
    evidence_type: str
    normalized_fields: dict[str, Any] = Field(default_factory=dict)
    citation_id: str
    redaction_state: str = "redacted"


class Hypothesis(BaseModel):
    statement: str
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_citations: list[str] = Field(default_factory=list)
    counter_evidence_citations: list[str] = Field(default_factory=list)


class RcaReport(BaseModel):
    top_hypotheses: list[Hypothesis] = Field(default_factory=list, min_length=1, max_length=3)
    likely_cause: str
    blast_radius: str
    recommended_manual_actions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class InvestigationRecord(BaseModel):
    id: str
    status: InvestigationStatus = InvestigationStatus.PENDING
    created_at: datetime
    updated_at: datetime
    alert: AlertEnvelope
    service_identity: ServiceIdentity | None = None
    plan: InvestigationPlan | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    report: RcaReport | None = None
    timeline: list[str] = Field(default_factory=list)


class MappingUpsertRequest(BaseModel):
    canonical_service_id: str
    provider: str
    provider_entity_id: str
    env: str


class LlmProviderRoute(BaseModel):
    tenant: str
    environment: str
    primary_model: str
    fallback_model: str
    key_ref: str


class EvalPrediction(BaseModel):
    hypotheses: list[Hypothesis]
    confidences: list[float]
    citations: list[str]
    latency_breakdown_ms: dict[str, int]


class GoldenIncident(BaseModel):
    id: str
    alert_input: AlertEnvelope
    expected_cause_label: str
    acceptable_synonyms: list[str] = Field(default_factory=list)
    severity: str


class AdjudicationRecord(BaseModel):
    investigation_id: str
    reviewer: str
    correctness_class: str
    notes: str | None = None
    created_at: datetime


class EvalRunRequest(BaseModel):
    dataset_path: str = "evals/golden-datasets/sample.json"


class EvalRunResult(BaseModel):
    id: str
    started_at: datetime
    ended_at: datetime | None = None
    top1_hit_rate: float = 0.0
    top3_hit_rate: float = 0.0
    unsupported_claim_rate: float = 0.0
    p95_latency_seconds: float = 0.0
    gate_passed: bool = False
    failures: list[str] = Field(default_factory=list)
