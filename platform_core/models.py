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


class StepExecutionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowStageId(str, Enum):
    RESOLVE_SERVICE_IDENTITY = "resolve_service_identity"
    BUILD_INVESTIGATION_PLAN = "build_investigation_plan"
    COLLECT_EVIDENCE = "collect_evidence"
    SYNTHESIZE_RCA_REPORT = "synthesize_rca_report"
    PUBLISH_REPORT = "publish_report"
    EMIT_EVAL_EVENT = "emit_eval_event"


class AgentRolloutMode(str, Enum):
    COMPARE = "compare"
    ACTIVE = "active"


class McpTransport(str, Enum):
    HTTP_SSE = "http_sse"


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


class StepLogEntry(BaseModel):
    timestamp: datetime
    level: str = "info"
    message: str


class AgentToolTrace(BaseModel):
    tool_name: str
    source: str
    read_only: bool = True
    started_at: datetime
    ended_at: datetime
    duration_ms: int = Field(default=0, ge=0)
    success: bool
    args_summary: dict[str, Any] = Field(default_factory=dict)
    result_summary: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    citations: list[str] = Field(default_factory=list)


class StepAttempt(BaseModel):
    attempt: int = Field(ge=1)
    status: StepExecutionStatus = StepExecutionStatus.QUEUED
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = None
    message: str | None = None
    error: str | None = None
    logs: list[StepLogEntry] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    stage_reasoning_summary: str | None = None
    tool_traces: list[AgentToolTrace] = Field(default_factory=list)


class WorkflowRunSummary(BaseModel):
    run_id: str
    investigation_id: str
    workflow_id: str | None = None
    status: InvestigationStatus = InvestigationStatus.PENDING
    started_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None
    current_stage: WorkflowStageId | None = None
    started_by: str = "system"


class WorkflowRunDetail(WorkflowRunSummary):
    stage_attempts: dict[WorkflowStageId, list[StepAttempt]] = Field(default_factory=dict)
    timeline: list[str] = Field(default_factory=list)
    events_count: int = 0


class WorkflowRunEvent(BaseModel):
    event_index: int | None = None
    run_id: str
    investigation_id: str
    workflow_id: str | None = None
    stage_id: WorkflowStageId | None = None
    stage_status: StepExecutionStatus
    run_status: InvestigationStatus | None = None
    attempt: int = Field(default=1, ge=1)
    timestamp: datetime
    message: str
    error: str | None = None
    citations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    logs: list[StepLogEntry] = Field(default_factory=list)
    stage_reasoning_summary: str | None = None
    tool_traces: list[AgentToolTrace] = Field(default_factory=list)


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
    active_run_id: str | None = None
    latest_run_status: InvestigationStatus | None = None


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


class ConnectorCredentialMode(str, Enum):
    SECRET_REF = "secret_ref"
    RAW_KEY = "raw_key"


class ConnectorCredentialUpsertRequest(BaseModel):
    tenant: str = "default"
    environment: str = "prod"
    mode: ConnectorCredentialMode = ConnectorCredentialMode.SECRET_REF
    secret_ref_name: str | None = None
    secret_ref_key: str | None = None
    raw_key: str | None = None


class ConnectorCredentialView(BaseModel):
    provider: str
    tenant: str
    environment: str
    mode: ConnectorCredentialMode
    secret_ref_name: str | None = None
    secret_ref_key: str | None = None
    key_last4: str | None = None
    updated_at: datetime
    updated_by: str


class ConnectionTestResult(BaseModel):
    provider: str
    tenant: str
    environment: str
    success: bool
    detail: str


class UserContext(BaseModel):
    user_id: str
    role: str
    tenant: str


class McpServerConfig(BaseModel):
    server_id: str
    tenant: str = "default"
    environment: str = "prod"
    transport: McpTransport = McpTransport.HTTP_SSE
    base_url: str
    secret_ref_name: str | None = None
    secret_ref_key: str | None = None
    timeout_seconds: int = Field(default=8, ge=1, le=60)
    enabled: bool = True
    updated_at: datetime
    updated_by: str


class McpServerUpsertRequest(BaseModel):
    tenant: str = "default"
    environment: str = "prod"
    transport: McpTransport = McpTransport.HTTP_SSE
    base_url: str
    secret_ref_name: str | None = None
    secret_ref_key: str | None = None
    timeout_seconds: int = Field(default=8, ge=1, le=60)
    enabled: bool = True


class McpToolDescriptor(BaseModel):
    server_id: str
    tool_name: str
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    read_only: bool = True
    light_probe: bool = False


class AgentPromptProfile(BaseModel):
    tenant: str = "default"
    environment: str = "prod"
    stage_id: WorkflowStageId
    system_prompt: str
    objective_template: str
    max_turns: int = Field(default=4, ge=1, le=20)
    max_tool_calls: int = Field(default=6, ge=1, le=40)
    tool_allowlist: list[str] = Field(default_factory=list)
    updated_at: datetime
    updated_by: str


class AgentPromptProfileUpsertRequest(BaseModel):
    tenant: str = "default"
    environment: str = "prod"
    system_prompt: str
    objective_template: str
    max_turns: int = Field(default=4, ge=1, le=20)
    max_tool_calls: int = Field(default=6, ge=1, le=40)
    tool_allowlist: list[str] = Field(default_factory=list)


class AgentRolloutConfig(BaseModel):
    tenant: str = "default"
    environment: str = "prod"
    mode: AgentRolloutMode = AgentRolloutMode.COMPARE
    updated_at: datetime
    updated_by: str


class WorkflowLayoutNode(BaseModel):
    id: str
    x: float
    y: float


class WorkflowViewport(BaseModel):
    x: float = 0
    y: float = 0
    zoom: float = 1


class WorkflowLayoutState(BaseModel):
    workflow_key: str
    tenant: str
    user_id: str
    nodes: list[WorkflowLayoutNode] = Field(default_factory=list)
    viewport: WorkflowViewport = Field(default_factory=WorkflowViewport)
    updated_at: datetime


class WorkflowLayoutUpsertRequest(BaseModel):
    nodes: list[WorkflowLayoutNode] = Field(default_factory=list)
    viewport: WorkflowViewport = Field(default_factory=WorkflowViewport)
