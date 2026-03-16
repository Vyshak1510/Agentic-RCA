from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from threading import Lock
from uuid import uuid4

from platform_core.mcp_execution import enrich_tool_descriptors
from platform_core.models import (
    AdjudicationRecord,
    AgentPromptProfile,
    AgentRolloutConfig,
    AgentRolloutMode,
    AlertEnvelope,
    ConnectorCredentialMode,
    ConnectorCredentialView,
    ContextArtifact,
    ContextChunk,
    ContextPack,
    ContextReference,
    EvidenceRequirement,
    EvidenceItem,
    EvalRunResult,
    Hypothesis,
    InvestigationRecord,
    InvestigationPlan,
    InvestigationTeamProfile,
    InvestigationStatus,
    LlmProviderRoute,
    McpServerConfig,
    McpToolDescriptor,
    MappingUpsertRequest,
    RcaReport,
    ServiceIdentity,
    StageMissionProfile,
    StepAttempt,
    StepExecutionStatus,
    StepLogEntry,
    TeamMissionProfile,
    WorkflowLayoutState,
    WorkflowRunDetail,
    WorkflowRunEvent,
    WorkflowStageId,
)


class InMemoryStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self.investigations: dict[str, InvestigationRecord] = {}
        self.dedupe_index: dict[str, tuple[str, datetime]] = {}
        self.mappings: dict[tuple[str, str], MappingUpsertRequest] = {}
        self.llm_routes: dict[tuple[str, str], LlmProviderRoute] = {}
        self.connector_credentials: dict[tuple[str, str, str], ConnectorCredentialView] = {}
        self.mcp_servers: dict[tuple[str, str, str], McpServerConfig] = {}
        self.mcp_tools: dict[tuple[str, str, str], list[McpToolDescriptor]] = {}
        self.investigation_teams: dict[tuple[str, str, str], InvestigationTeamProfile] = {}
        self.agent_prompt_profiles: dict[tuple[str, str, WorkflowStageId], AgentPromptProfile] = {}
        self.agent_rollout_configs: dict[tuple[str, str], AgentRolloutConfig] = {}
        self.stage_missions: dict[tuple[str, str, WorkflowStageId], StageMissionProfile] = {}
        self.team_missions: dict[tuple[str, str, str], TeamMissionProfile] = {}
        self.context_pack_versions: dict[tuple[str, str, str], list[ContextPack]] = defaultdict(list)
        self.active_context_pack: dict[tuple[str, str], tuple[str, int]] = {}
        self.workflow_layouts: dict[tuple[str, str, str], WorkflowLayoutState] = {}
        self.eval_runs: dict[str, EvalRunResult] = {}
        self.adjudications: list[AdjudicationRecord] = []
        self.counters: defaultdict[str, int] = defaultdict(int)

        self.runs_by_investigation: dict[str, list[str]] = defaultdict(list)
        self.run_details: dict[str, WorkflowRunDetail] = {}
        self.run_events: dict[str, list[WorkflowRunEvent]] = defaultdict(list)

        now = datetime.now(timezone.utc)
        self.llm_routes[("default", "prod")] = LlmProviderRoute(
            tenant="default",
            environment="prod",
            primary_model="codex",
            fallback_model="claude",
            key_ref="llm-provider-secret",
        )
        for stage_id in (WorkflowStageId.RESOLVE_SERVICE_IDENTITY, WorkflowStageId.BUILD_INVESTIGATION_PLAN):
            self.agent_prompt_profiles[("default", "prod", stage_id)] = AgentPromptProfile(
                tenant="default",
                environment="prod",
                stage_id=stage_id,
                system_prompt="You are an RCA investigation agent. Use read-only tools and cite evidence.",
                objective_template="Resolve alert {{incident_key}} with bounded, evidence-linked reasoning.",
                max_turns=4,
                max_tool_calls=6,
                tool_allowlist=["mcp.grafana.*", "mcp.jaeger.*", "mcp.prometheus.*"],
                updated_at=now,
                updated_by="system",
            )
        self.agent_rollout_configs[("default", "prod")] = AgentRolloutConfig(
            tenant="default",
            environment="prod",
            mode=AgentRolloutMode.COMPARE,
            updated_at=now,
            updated_by="system",
        )
        self.stage_missions[("default", "prod", WorkflowStageId.RESOLVE_SERVICE_IDENTITY)] = StageMissionProfile(
            tenant="default",
            environment="prod",
            stage_id=WorkflowStageId.RESOLVE_SERVICE_IDENTITY,
            mission_objective=(
                "Resolve canonical service identity from alert context and available discovery tools. "
                "If confidence is low, preserve ambiguity explicitly."
            ),
            required_checks=["alert_entities_reviewed", "canonical_service_selected"],
            allowed_tools=["mcp.grafana.*", "mcp.jaeger.*", "mcp.prometheus.*"],
            completion_criteria=["confidence_reported", "ambiguity_listed_when_present"],
            unknown_not_available_rules=["missing_entity_context"],
            relevance_weights={"alert": 1.0, "context_pack": 0.7, "tool_discovery": 0.8},
            alias_priority_order=["entity_ids", "explicit_service", "title", "summary"],
            alias_min_confidence=0.72,
            summary_tiebreak_only=True,
            updated_at=now,
            updated_by="system",
        )
        self.stage_missions[("default", "prod", WorkflowStageId.BUILD_INVESTIGATION_PLAN)] = StageMissionProfile(
            tenant="default",
            environment="prod",
            stage_id=WorkflowStageId.BUILD_INVESTIGATION_PLAN,
            mission_objective=(
                "Create an MCP-only bounded plan aligned to service scope and budget limits. "
                "Planning must use discovery/light probes, not deep evidence reads."
            ),
            required_checks=["mcp_only_steps", "budget_limits_applied", "target_service_scoped"],
            allowed_tools=["mcp.grafana.*", "mcp.jaeger.*", "mcp.prometheus.*"],
            completion_criteria=["ordered_steps_present", "max_api_calls_present"],
            unknown_not_available_rules=["no_invocable_tools"],
            relevance_weights={"alert": 1.0, "context_pack": 0.7, "tool_catalog": 0.9},
            alias_priority_order=["entity_ids", "explicit_service", "title", "summary"],
            alias_min_confidence=0.72,
            summary_tiebreak_only=True,
            updated_at=now,
            updated_by="system",
        )
        self.stage_missions[("default", "prod", WorkflowStageId.COLLECT_EVIDENCE)] = StageMissionProfile(
            tenant="default",
            environment="prod",
            stage_id=WorkflowStageId.COLLECT_EVIDENCE,
            mission_objective=(
                "Collect evidence in parallel with tool-owned teams while preserving read-only MCP-only policy."
            ),
            required_checks=["team_execution_completed", "citations_created"],
            allowed_tools=["mcp.grafana.*", "mcp.jaeger.*", "mcp.prometheus.*"],
            completion_criteria=["evidence_items_collected", "team_reports_produced"],
            unknown_not_available_rules=["team_no_tools", "missing_required_signals"],
            relevance_weights={"service_scoped": 1.0, "global": 0.6},
            updated_at=now,
            updated_by="system",
        )
        self.stage_missions[("default", "prod", WorkflowStageId.SYNTHESIZE_RCA_REPORT)] = StageMissionProfile(
            tenant="default",
            environment="prod",
            stage_id=WorkflowStageId.SYNTHESIZE_RCA_REPORT,
            mission_objective="Arbitrate team drafts and produce final top-3 RCA with strict citation backing.",
            required_checks=["top3_generated", "citations_attached"],
            allowed_tools=[],
            completion_criteria=["likely_cause_present", "manual_actions_present"],
            unknown_not_available_rules=["insufficient_citations"],
            relevance_weights={"team_reports": 1.0, "raw_evidence": 0.9, "context_pack": 0.5},
            updated_at=now,
            updated_by="system",
        )
        self.stage_missions[("default", "prod", WorkflowStageId.EMIT_EVAL_EVENT)] = StageMissionProfile(
            tenant="default",
            environment="prod",
            stage_id=WorkflowStageId.EMIT_EVAL_EVENT,
            mission_objective="Emit eval-ready telemetry and structured artifacts for offline/online scoring.",
            required_checks=["latency_emitted", "citation_metrics_emitted"],
            allowed_tools=[],
            completion_criteria=["eval_payload_valid"],
            unknown_not_available_rules=["missing_eval_dimensions"],
            relevance_weights={"metrics": 1.0, "training_artifacts": 0.8},
            updated_at=now,
            updated_by="system",
        )
        self.investigation_teams[("default", "prod", "app")] = InvestigationTeamProfile(
            team_id="app",
            tenant="default",
            environment="prod",
            enabled=True,
            objective_prompt=(
                "Application team: investigate service-level errors, trace exceptions, and API behavior. "
                "Produce concise hypotheses with citations."
            ),
            tool_allowlist=["mcp.jaeger.*"],
            max_tool_calls=6,
            max_parallel_calls=3,
            timeout_seconds=45,
            updated_at=now,
            updated_by="system",
        )
        self.team_missions[("default", "prod", "app")] = TeamMissionProfile(
            team_id="app",
            tenant="default",
            environment="prod",
            mission_objective=(
                "Application team investigates request path failures, exceptions, retries, and dependency call errors."
            ),
            required_checks=["trace_errors_checked", "service_operations_reviewed"],
            allowed_tools=["mcp.jaeger.*"],
            completion_criteria=["mini_rca_produced"],
            unknown_not_available_rules=["no_trace_evidence"],
            relevance_weights={"service_scoped": 1.0, "global": 0.4},
            updated_at=now,
            updated_by="system",
        )
        self.team_missions[("default", "prod", "infra")] = TeamMissionProfile(
            team_id="infra",
            tenant="default",
            environment="prod",
            mission_objective=(
                "Infrastructure team investigates platform saturation, deployment events, and shared infra health."
            ),
            required_checks=["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
            allowed_tools=["mcp.grafana.*", "mcp.prometheus.*"],
            completion_criteria=["infra_completeness_reported"],
            unknown_not_available_rules=["missing_infra_signals", "missing_required_checks"],
            relevance_weights={"service_scoped": 1.0, "global": 0.8},
            evidence_requirements=[
                EvidenceRequirement(
                    evidence_class="annotation_change_context",
                    description="Check deployment, annotation, or maintenance context around the incident window.",
                    tool_patterns=["mcp.grafana.get_annotations", "mcp.grafana.get_annotation_tags"],
                    query_scope="change",
                ),
                EvidenceRequirement(
                    evidence_class="local_service_metrics",
                    description="Validate latency/error/throughput metrics for the resolved service.",
                    tool_patterns=["mcp.prometheus.query_range", "mcp.prometheus.query_instant"],
                    query_scope="service",
                    required_symptoms=["latency", "resource", "memory", "cpu", "error"],
                ),
                EvidenceRequirement(
                    evidence_class="global_shared_metrics",
                    description="Validate whether the anomaly is shared across the platform or isolated to the service.",
                    tool_patterns=["mcp.prometheus.query_range", "mcp.prometheus.query_instant"],
                    query_scope="global",
                    required_symptoms=["latency", "resource", "memory", "cpu", "error"],
                ),
            ],
            symptom_overrides={
                "latency": ["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
                "resource": ["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
                "memory": ["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
                "cpu": ["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
                "error": ["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
            },
            updated_at=now,
            updated_by="system",
        )
        self.team_missions[("default", "prod", "db")] = TeamMissionProfile(
            team_id="db",
            tenant="default",
            environment="prod",
            mission_objective="Database team investigates query latency, lock/contention, and datastore health.",
            required_checks=["db_signal_checked"],
            allowed_tools=[],
            completion_criteria=["db_or_unknown_reported"],
            unknown_not_available_rules=["no_db_tools_configured"],
            relevance_weights={"service_scoped": 1.0, "global": 0.6},
            updated_at=now,
            updated_by="system",
        )
        self.investigation_teams[("default", "prod", "infra")] = InvestigationTeamProfile(
            team_id="infra",
            tenant="default",
            environment="prod",
            enabled=True,
            objective_prompt=(
                "Infrastructure team: investigate platform health, saturation, and latency indicators from observability tools."
            ),
            tool_allowlist=["mcp.grafana.*", "mcp.prometheus.*"],
            max_tool_calls=6,
            max_parallel_calls=3,
            timeout_seconds=45,
            updated_at=now,
            updated_by="system",
        )
        self.investigation_teams[("default", "prod", "db")] = InvestigationTeamProfile(
            team_id="db",
            tenant="default",
            environment="prod",
            enabled=True,
            objective_prompt=(
                "Database team: investigate datastore contention, query regressions, and dependency health."
            ),
            tool_allowlist=[],
            max_tool_calls=4,
            max_parallel_calls=2,
            timeout_seconds=45,
            updated_at=now,
            updated_by="system",
        )

        self._state_path = os.getenv("RCA_STORE_STATE_PATH", "/workspace/.data/store-state.json").strip()
        self._load_persisted_state()

    @staticmethod
    def _default_agent_prompt_profile(
        tenant: str,
        environment: str,
        stage_id: WorkflowStageId,
        *,
        updated_by: str = "system",
    ) -> AgentPromptProfile:
        now = datetime.now(timezone.utc)
        return AgentPromptProfile(
            tenant=tenant,
            environment=environment,
            stage_id=stage_id,
            system_prompt="You are an RCA investigation agent. Use read-only tools and cite evidence.",
            objective_template="Resolve alert {{incident_key}} with bounded, evidence-linked reasoning.",
            max_turns=4,
            max_tool_calls=6,
            tool_allowlist=["mcp.grafana.*", "mcp.jaeger.*", "mcp.prometheus.*"],
            updated_at=now,
            updated_by=updated_by,
        )

    @staticmethod
    def _default_stage_mission_profile(
        tenant: str,
        environment: str,
        stage_id: WorkflowStageId,
        *,
        updated_by: str = "system",
    ) -> StageMissionProfile:
        now = datetime.now(timezone.utc)
        defaults: dict[WorkflowStageId, dict[str, object]] = {
            WorkflowStageId.RESOLVE_SERVICE_IDENTITY: {
                "mission_objective": (
                    "Resolve canonical service identity from alert context and available discovery tools. "
                    "If confidence is low, preserve ambiguity explicitly."
                ),
                "required_checks": ["alert_entities_reviewed", "canonical_service_selected"],
                "allowed_tools": ["mcp.grafana.*", "mcp.jaeger.*", "mcp.prometheus.*"],
                "completion_criteria": ["confidence_reported", "ambiguity_listed_when_present"],
                "unknown_not_available_rules": ["missing_entity_context"],
                "relevance_weights": {"alert": 1.0, "context_pack": 0.7, "tool_discovery": 0.8},
                "alias_priority_order": ["entity_ids", "explicit_service", "title", "summary"],
                "alias_min_confidence": 0.72,
                "summary_tiebreak_only": True,
            },
            WorkflowStageId.BUILD_INVESTIGATION_PLAN: {
                "mission_objective": (
                    "Create an MCP-only bounded plan aligned to service scope and budget limits. "
                    "Planning must use discovery/light probes, not deep evidence reads."
                ),
                "required_checks": ["mcp_only_steps", "budget_limits_applied", "target_service_scoped"],
                "allowed_tools": ["mcp.grafana.*", "mcp.jaeger.*", "mcp.prometheus.*"],
                "completion_criteria": ["ordered_steps_present", "max_api_calls_present"],
                "unknown_not_available_rules": ["no_invocable_tools"],
                "relevance_weights": {"alert": 1.0, "context_pack": 0.7, "tool_catalog": 0.9},
                "alias_priority_order": ["entity_ids", "explicit_service", "title", "summary"],
                "alias_min_confidence": 0.72,
                "summary_tiebreak_only": True,
            },
            WorkflowStageId.COLLECT_EVIDENCE: {
                "mission_objective": "Collect evidence in parallel with tool-owned teams while preserving read-only MCP-only policy.",
                "required_checks": ["team_execution_completed", "citations_created"],
                "allowed_tools": ["mcp.grafana.*", "mcp.jaeger.*", "mcp.prometheus.*"],
                "completion_criteria": ["evidence_items_collected", "team_reports_produced"],
                "unknown_not_available_rules": ["team_no_tools", "missing_required_signals"],
                "relevance_weights": {"service_scoped": 1.0, "global": 0.6},
                "alias_priority_order": [],
                "alias_min_confidence": 0.7,
                "summary_tiebreak_only": True,
            },
            WorkflowStageId.SYNTHESIZE_RCA_REPORT: {
                "mission_objective": "Arbitrate team drafts and produce final top-3 RCA with strict citation backing.",
                "required_checks": ["top3_generated", "citations_attached"],
                "allowed_tools": [],
                "completion_criteria": ["likely_cause_present", "manual_actions_present"],
                "unknown_not_available_rules": ["insufficient_citations"],
                "relevance_weights": {"team_reports": 1.0, "raw_evidence": 0.9, "context_pack": 0.5},
                "alias_priority_order": [],
                "alias_min_confidence": 0.7,
                "summary_tiebreak_only": True,
            },
            WorkflowStageId.EMIT_EVAL_EVENT: {
                "mission_objective": "Emit eval-ready telemetry and structured artifacts for offline/online scoring.",
                "required_checks": ["latency_emitted", "citation_metrics_emitted"],
                "allowed_tools": [],
                "completion_criteria": ["eval_payload_valid"],
                "unknown_not_available_rules": ["missing_eval_dimensions"],
                "relevance_weights": {"metrics": 1.0, "training_artifacts": 0.8},
                "alias_priority_order": [],
                "alias_min_confidence": 0.7,
                "summary_tiebreak_only": True,
            },
        }
        selected = defaults[stage_id]
        return StageMissionProfile(
            tenant=tenant,
            environment=environment,
            stage_id=stage_id,
            mission_objective=str(selected["mission_objective"]),
            required_checks=list(selected["required_checks"]),
            allowed_tools=list(selected["allowed_tools"]),
            completion_criteria=list(selected["completion_criteria"]),
            unknown_not_available_rules=list(selected["unknown_not_available_rules"]),
            relevance_weights=dict(selected["relevance_weights"]),
            alias_priority_order=list(selected["alias_priority_order"]),
            alias_min_confidence=float(selected["alias_min_confidence"]),
            summary_tiebreak_only=bool(selected["summary_tiebreak_only"]),
            updated_at=now,
            updated_by=updated_by,
        )

    @staticmethod
    def _default_team_mission_profile(
        tenant: str,
        environment: str,
        team_id: str,
        *,
        updated_by: str = "system",
    ) -> TeamMissionProfile:
        now = datetime.now(timezone.utc)
        defaults: dict[str, dict[str, object]] = {
            "app": {
                "mission_objective": (
                    "Application team investigates request path failures, exceptions, retries, and dependency call errors."
                ),
                "required_checks": ["trace_errors_checked", "service_operations_reviewed"],
                "allowed_tools": ["mcp.jaeger.*"],
                "completion_criteria": ["mini_rca_produced"],
                "unknown_not_available_rules": ["no_trace_evidence"],
                "relevance_weights": {"service_scoped": 1.0, "global": 0.4},
                "evidence_requirements": [],
                "symptom_overrides": {},
            },
            "infra": {
                "mission_objective": (
                    "Infrastructure team investigates platform saturation, deployment events, and shared infra health."
                ),
                "required_checks": ["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
                "allowed_tools": ["mcp.grafana.*", "mcp.prometheus.*"],
                "completion_criteria": ["infra_completeness_reported"],
                "unknown_not_available_rules": ["missing_infra_signals", "missing_required_checks"],
                "relevance_weights": {"service_scoped": 1.0, "global": 0.8},
                "evidence_requirements": [
                    EvidenceRequirement(
                        evidence_class="annotation_change_context",
                        description="Check deployment, annotation, or maintenance context around the incident window.",
                        tool_patterns=["mcp.grafana.get_annotations", "mcp.grafana.get_annotation_tags"],
                        query_scope="change",
                    ),
                    EvidenceRequirement(
                        evidence_class="local_service_metrics",
                        description="Validate latency/error/throughput metrics for the resolved service.",
                        tool_patterns=["mcp.prometheus.query_range", "mcp.prometheus.query_instant"],
                        query_scope="service",
                        required_symptoms=["latency", "resource", "memory", "cpu", "error"],
                    ),
                    EvidenceRequirement(
                        evidence_class="global_shared_metrics",
                        description="Validate whether the anomaly is shared across the platform or isolated to the service.",
                        tool_patterns=["mcp.prometheus.query_range", "mcp.prometheus.query_instant"],
                        query_scope="global",
                        required_symptoms=["latency", "resource", "memory", "cpu", "error"],
                    ),
                ],
                "symptom_overrides": {
                    "latency": ["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
                    "resource": ["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
                    "memory": ["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
                    "cpu": ["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
                    "error": ["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
                },
            },
            "db": {
                "mission_objective": "Database team investigates query latency, lock/contention, and datastore health.",
                "required_checks": ["db_signal_checked"],
                "allowed_tools": [],
                "completion_criteria": ["db_or_unknown_reported"],
                "unknown_not_available_rules": ["no_db_tools_configured"],
                "relevance_weights": {"service_scoped": 1.0, "global": 0.6},
                "evidence_requirements": [],
                "symptom_overrides": {},
            },
        }
        selected = defaults[team_id]
        return TeamMissionProfile(
            team_id=team_id,
            tenant=tenant,
            environment=environment,
            mission_objective=str(selected["mission_objective"]),
            required_checks=list(selected["required_checks"]),
            allowed_tools=list(selected["allowed_tools"]),
            completion_criteria=list(selected["completion_criteria"]),
            unknown_not_available_rules=list(selected["unknown_not_available_rules"]),
            relevance_weights=dict(selected["relevance_weights"]),
            evidence_requirements=[
                item if isinstance(item, EvidenceRequirement) else EvidenceRequirement.model_validate(item)
                for item in selected["evidence_requirements"]
            ],
            symptom_overrides=dict(selected["symptom_overrides"]),
            updated_at=now,
            updated_by=updated_by,
        )

    def _reconcile_stage_mission(self, profile: StageMissionProfile) -> StageMissionProfile:
        default = self._default_stage_mission_profile(profile.tenant, profile.environment, profile.stage_id)
        if profile.updated_by == "system":
            return default.model_copy(update={"updated_at": profile.updated_at, "updated_by": profile.updated_by})
        return profile.model_copy(
            update={
                "alias_priority_order": profile.alias_priority_order or default.alias_priority_order,
                "alias_min_confidence": profile.alias_min_confidence or default.alias_min_confidence,
                "summary_tiebreak_only": profile.summary_tiebreak_only if profile.summary_tiebreak_only is not None else default.summary_tiebreak_only,
            }
        )

    def _reconcile_agent_prompt_profile(self, profile: AgentPromptProfile) -> AgentPromptProfile:
        default = self._default_agent_prompt_profile(profile.tenant, profile.environment, profile.stage_id)
        if profile.updated_by == "system":
            return default.model_copy(update={"updated_at": profile.updated_at, "updated_by": profile.updated_by})
        return profile.model_copy(
            update={
                "tool_allowlist": profile.tool_allowlist or default.tool_allowlist,
            }
        )

    def _reconcile_team_mission(self, profile: TeamMissionProfile) -> TeamMissionProfile:
        default = self._default_team_mission_profile(profile.tenant, profile.environment, profile.team_id)
        if profile.updated_by == "system":
            return default.model_copy(update={"updated_at": profile.updated_at, "updated_by": profile.updated_by})
        return profile.model_copy(
            update={
                "evidence_requirements": profile.evidence_requirements or default.evidence_requirements,
                "symptom_overrides": profile.symptom_overrides or default.symptom_overrides,
            }
        )

    def persist_state(self) -> None:
        self._persist_state()

    def _state_snapshot(self) -> dict[str, object]:
        return {
            "version": 1,
            "mappings": [item.model_dump(mode="json") for item in self.mappings.values()],
            "llm_routes": [item.model_dump(mode="json") for item in self.llm_routes.values()],
            "connector_credentials": [item.model_dump(mode="json") for item in self.connector_credentials.values()],
            "mcp_servers": [item.model_dump(mode="json") for item in self.mcp_servers.values()],
            "mcp_tools": [
                {
                    "tenant": tenant,
                    "environment": environment,
                    "server_id": server_id,
                    "tools": [tool.model_dump(mode="json") for tool in tools],
                }
                for (tenant, environment, server_id), tools in self.mcp_tools.items()
            ],
            "investigation_teams": [item.model_dump(mode="json") for item in self.investigation_teams.values()],
            "agent_prompt_profiles": [item.model_dump(mode="json") for item in self.agent_prompt_profiles.values()],
            "agent_rollout_configs": [item.model_dump(mode="json") for item in self.agent_rollout_configs.values()],
            "stage_missions": [item.model_dump(mode="json") for item in self.stage_missions.values()],
            "team_missions": [item.model_dump(mode="json") for item in self.team_missions.values()],
            "context_pack_versions": [
                {
                    "tenant": tenant,
                    "environment": environment,
                    "pack_id": pack_id,
                    "versions": [item.model_dump(mode="json") for item in versions],
                }
                for (tenant, environment, pack_id), versions in self.context_pack_versions.items()
            ],
            "active_context_pack": [
                {
                    "tenant": tenant,
                    "environment": environment,
                    "pack_id": pack_id,
                    "version": version,
                }
                for (tenant, environment), (pack_id, version) in self.active_context_pack.items()
            ],
            "workflow_layouts": [item.model_dump(mode="json") for item in self.workflow_layouts.values()],
        }

    def _persist_state(self) -> None:
        if not self._state_path:
            return
        path = Path(self._state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._state_snapshot(), ensure_ascii=True)
        tmp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(path)

    def _load_persisted_state(self) -> None:
        if not self._state_path:
            return
        path = Path(self._state_path)
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return

        if isinstance(payload.get("mappings"), list):
            self.mappings = {}
            for item in payload["mappings"]:
                if not isinstance(item, dict):
                    continue
                mapping = MappingUpsertRequest.model_validate(item)
                self.mappings[(mapping.provider, mapping.provider_entity_id)] = mapping

        if isinstance(payload.get("llm_routes"), list):
            self.llm_routes = {}
            for item in payload["llm_routes"]:
                if not isinstance(item, dict):
                    continue
                route = LlmProviderRoute.model_validate(item)
                self.llm_routes[(route.tenant, route.environment)] = route

        if isinstance(payload.get("connector_credentials"), list):
            self.connector_credentials = {}
            for item in payload["connector_credentials"]:
                if not isinstance(item, dict):
                    continue
                cred = ConnectorCredentialView.model_validate(item)
                self.connector_credentials[(cred.tenant, cred.environment, cred.provider)] = cred

        if isinstance(payload.get("mcp_servers"), list):
            self.mcp_servers = {}
            for item in payload["mcp_servers"]:
                if not isinstance(item, dict):
                    continue
                config = McpServerConfig.model_validate(item)
                self.mcp_servers[(config.tenant, config.environment, config.server_id)] = config

        if isinstance(payload.get("mcp_tools"), list):
            self.mcp_tools = {}
            for block in payload["mcp_tools"]:
                if not isinstance(block, dict):
                    continue
                tenant = str(block.get("tenant") or "").strip()
                environment = str(block.get("environment") or "").strip()
                server_id = str(block.get("server_id") or "").strip()
                if not tenant or not environment or not server_id:
                    continue
                tools_payload = block.get("tools")
                if not isinstance(tools_payload, list):
                    continue
                tools = [McpToolDescriptor.model_validate(item) for item in tools_payload if isinstance(item, dict)]
                self.mcp_tools[(tenant, environment, server_id)] = tools

        if isinstance(payload.get("investigation_teams"), list):
            self.investigation_teams = {}
            for item in payload["investigation_teams"]:
                if not isinstance(item, dict):
                    continue
                team = InvestigationTeamProfile.model_validate(item)
                self.investigation_teams[(team.tenant, team.environment, team.team_id)] = team

        if isinstance(payload.get("agent_prompt_profiles"), list):
            self.agent_prompt_profiles = {}
            for item in payload["agent_prompt_profiles"]:
                if not isinstance(item, dict):
                    continue
                profile = AgentPromptProfile.model_validate(item)
                self.agent_prompt_profiles[(profile.tenant, profile.environment, profile.stage_id)] = profile

        if isinstance(payload.get("agent_rollout_configs"), list):
            self.agent_rollout_configs = {}
            for item in payload["agent_rollout_configs"]:
                if not isinstance(item, dict):
                    continue
                rollout = AgentRolloutConfig.model_validate(item)
                self.agent_rollout_configs[(rollout.tenant, rollout.environment)] = rollout

        if isinstance(payload.get("stage_missions"), list):
            self.stage_missions = {}
            for item in payload["stage_missions"]:
                if not isinstance(item, dict):
                    continue
                mission = StageMissionProfile.model_validate(item)
                self.stage_missions[(mission.tenant, mission.environment, mission.stage_id)] = mission

        if isinstance(payload.get("team_missions"), list):
            self.team_missions = {}
            for item in payload["team_missions"]:
                if not isinstance(item, dict):
                    continue
                mission = TeamMissionProfile.model_validate(item)
                self.team_missions[(mission.tenant, mission.environment, mission.team_id)] = mission

        if isinstance(payload.get("context_pack_versions"), list):
            self.context_pack_versions = defaultdict(list)
            for block in payload["context_pack_versions"]:
                if not isinstance(block, dict):
                    continue
                tenant = str(block.get("tenant") or "").strip()
                environment = str(block.get("environment") or "").strip()
                pack_id = str(block.get("pack_id") or "").strip()
                versions_payload = block.get("versions")
                if not tenant or not environment or not pack_id or not isinstance(versions_payload, list):
                    continue
                versions = [ContextPack.model_validate(item) for item in versions_payload if isinstance(item, dict)]
                self.context_pack_versions[(tenant, environment, pack_id)] = versions

        if isinstance(payload.get("active_context_pack"), list):
            self.active_context_pack = {}
            for block in payload["active_context_pack"]:
                if not isinstance(block, dict):
                    continue
                tenant = str(block.get("tenant") or "").strip()
                environment = str(block.get("environment") or "").strip()
                pack_id = str(block.get("pack_id") or "").strip()
                version = block.get("version")
                if not tenant or not environment or not pack_id or not isinstance(version, int):
                    continue
                self.active_context_pack[(tenant, environment)] = (pack_id, version)

        if isinstance(payload.get("workflow_layouts"), list):
            self.workflow_layouts = {}
            for item in payload["workflow_layouts"]:
                if not isinstance(item, dict):
                    continue
                layout = WorkflowLayoutState.model_validate(item)
                self.workflow_layouts[(layout.tenant, layout.user_id, layout.workflow_key)] = layout

    @staticmethod
    def _fingerprint(alert: AlertEnvelope) -> str:
        parts = [alert.incident_key, alert.severity, *sorted(alert.entity_ids)]
        return sha256("|".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _duration_ms(started_at: datetime | None, ended_at: datetime | None) -> int | None:
        if not started_at or not ended_at:
            return None
        return int((ended_at - started_at).total_seconds() * 1000)

    @staticmethod
    def _mission_id(prefix: str, tenant: str, environment: str, key: str) -> str:
        return f"{prefix}:{tenant}:{environment}:{key}"

    @staticmethod
    def _chunk_text(content: str, chunk_size: int = 900) -> list[str]:
        text = (content or "").strip()
        if not text:
            return []
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        chunks: list[str] = []
        buffer = ""
        for paragraph in paragraphs:
            candidate = paragraph if not buffer else f"{buffer}\n\n{paragraph}"
            if len(candidate) <= chunk_size:
                buffer = candidate
                continue
            if buffer:
                chunks.append(buffer)
            if len(paragraph) <= chunk_size:
                buffer = paragraph
                continue
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start : start + chunk_size])
                start += chunk_size
            buffer = ""
        if buffer:
            chunks.append(buffer)
        return chunks[:120]

    @staticmethod
    def _context_terms_from_alert(alert_payload: dict[str, object] | None) -> set[str]:
        if not isinstance(alert_payload, dict):
            return set()
        terms: set[str] = set()
        for key in ("incident_key", "source", "severity", "service", "service_name", "canonical_service_id"):
            value = alert_payload.get(key)
            if isinstance(value, str) and value.strip():
                terms.add(value.strip().lower())
        entity_ids = alert_payload.get("entity_ids")
        if isinstance(entity_ids, list):
            for item in entity_ids:
                if isinstance(item, str) and item.strip():
                    terms.add(item.strip().lower())
        raw_payload = alert_payload.get("raw_payload")
        if isinstance(raw_payload, dict):
            for key in ("service", "service_name", "component", "namespace", "team", "alertname"):
                value = raw_payload.get(key)
                if isinstance(value, str) and value.strip():
                    terms.add(value.strip().lower())
        return terms

    @staticmethod
    def _context_score(text: str, terms: set[str], stage_id: WorkflowStageId | None, team_id: str | None) -> float:
        lowered = text.lower()
        score = 0.05
        for term in terms:
            if term in lowered:
                score += 0.4
        if stage_id and stage_id.value.replace("_", " ") in lowered:
            score += 0.2
        if team_id and team_id.lower() in lowered:
            score += 0.2
        return round(min(score, 1.0), 4)

    def record_alert(self, investigation: InvestigationRecord) -> tuple[str, bool]:
        with self._lock:
            fingerprint = self._fingerprint(investigation.alert)
            now = datetime.now(timezone.utc)
            if fingerprint in self.dedupe_index:
                existing_id, seen_at = self.dedupe_index[fingerprint]
                if now - seen_at <= timedelta(minutes=15):
                    return existing_id, True

            self.investigations[investigation.id] = investigation
            self.dedupe_index[fingerprint] = (investigation.id, now)
            self.counters["alerts_ingested_total"] += 1
            return investigation.id, False

    def get_investigation(self, investigation_id: str) -> InvestigationRecord | None:
        return self.investigations.get(investigation_id)

    def update_status(self, investigation_id: str, status: InvestigationStatus) -> InvestigationRecord | None:
        with self._lock:
            investigation = self.investigations.get(investigation_id)
            if not investigation:
                return None
            investigation.status = status
            investigation.latest_run_status = status
            investigation.updated_at = datetime.now(timezone.utc)
            investigation.timeline.append(f"Status updated to {status.value}")
            return investigation

    def list_investigations(
        self,
        status: InvestigationStatus | None = None,
        source: str | None = None,
        severity: str | None = None,
        tenant: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[InvestigationRecord], int]:
        _ = tenant  # Reserved for multi-tenant backend persistence integration.

        items = list(self.investigations.values())
        if status:
            items = [item for item in items if item.status == status]
        if source:
            items = [item for item in items if item.alert.source == source]
        if severity:
            items = [item for item in items if item.alert.severity == severity]

        items = sorted(items, key=lambda item: item.updated_at, reverse=True)
        total = len(items)
        offset = max(page - 1, 0) * page_size
        paginated = items[offset : offset + page_size]
        return paginated, total

    def create_run(
        self,
        investigation_id: str,
        run_id: str,
        workflow_id: str | None,
        started_by: str,
        started_at: datetime | None = None,
    ) -> WorkflowRunDetail | None:
        with self._lock:
            investigation = self.investigations.get(investigation_id)
            if not investigation:
                return None

            now = started_at or datetime.now(timezone.utc)
            run_detail = WorkflowRunDetail(
                run_id=run_id,
                investigation_id=investigation_id,
                workflow_id=workflow_id,
                status=InvestigationStatus.RUNNING,
                started_at=now,
                updated_at=now,
                started_by=started_by,
            )

            self.run_details[run_id] = run_detail
            self.run_events[run_id] = []
            self.runs_by_investigation[investigation_id].insert(0, run_id)

            investigation.active_run_id = run_id
            investigation.status = InvestigationStatus.RUNNING
            investigation.latest_run_status = InvestigationStatus.RUNNING
            investigation.updated_at = now
            investigation.timeline.append(f"Run {run_id} started")

            return run_detail

    def list_runs(self, investigation_id: str) -> list[WorkflowRunDetail]:
        run_ids = self.runs_by_investigation.get(investigation_id, [])
        return [self.run_details[run_id] for run_id in run_ids if run_id in self.run_details]

    def get_run(self, run_id: str) -> WorkflowRunDetail | None:
        return self.run_details.get(run_id)

    def get_run_for_investigation(self, investigation_id: str, run_id: str) -> WorkflowRunDetail | None:
        run = self.run_details.get(run_id)
        if not run or run.investigation_id != investigation_id:
            return None
        return run

    def get_active_run(self, investigation_id: str) -> WorkflowRunDetail | None:
        investigation = self.investigations.get(investigation_id)
        if not investigation or not investigation.active_run_id:
            return None
        return self.run_details.get(investigation.active_run_id)

    def set_run_workflow_id(self, run_id: str, workflow_id: str) -> WorkflowRunDetail | None:
        with self._lock:
            run = self.run_details.get(run_id)
            if not run:
                return None
            run.workflow_id = workflow_id
            run.updated_at = datetime.now(timezone.utc)
            return run

    def append_run_event(self, run_id: str, event: WorkflowRunEvent) -> WorkflowRunEvent | None:
        with self._lock:
            run = self.run_details.get(run_id)
            if not run:
                return None

            events = self.run_events[run_id]
            stored_event = event.model_copy(update={"event_index": len(events) + 1})
            events.append(stored_event)

            run.events_count = len(events)
            run.updated_at = stored_event.timestamp
            run.timeline.append(stored_event.message)

            if stored_event.workflow_id and not run.workflow_id:
                run.workflow_id = stored_event.workflow_id

            if stored_event.stage_id is not None:
                run.current_stage = stored_event.stage_id
                attempts = run.stage_attempts.setdefault(stored_event.stage_id, [])
                while len(attempts) < stored_event.attempt:
                    attempts.append(StepAttempt(attempt=len(attempts) + 1))

                attempt = attempts[stored_event.attempt - 1]
                attempt.status = stored_event.stage_status
                attempt.message = stored_event.message
                attempt.error = stored_event.error
                attempt.stage_reasoning_summary = stored_event.stage_reasoning_summary

                if stored_event.stage_status == StepExecutionStatus.RUNNING and not attempt.started_at:
                    attempt.started_at = stored_event.timestamp

                if stored_event.stage_status in {
                    StepExecutionStatus.COMPLETED,
                    StepExecutionStatus.FAILED,
                    StepExecutionStatus.SKIPPED,
                }:
                    if not attempt.ended_at:
                        attempt.ended_at = stored_event.timestamp
                    attempt.duration_ms = self._duration_ms(attempt.started_at, attempt.ended_at)

                seen = set(attempt.citations)
                for citation in stored_event.citations:
                    if citation not in seen:
                        attempt.citations.append(citation)
                        seen.add(citation)

                attempt.metadata.update(stored_event.metadata)
                if stored_event.tool_traces:
                    attempt.tool_traces = stored_event.tool_traces
                if stored_event.logs:
                    attempt.logs.extend(stored_event.logs)
                else:
                    attempt.logs.append(
                        StepLogEntry(
                            timestamp=stored_event.timestamp,
                            level="error" if stored_event.error else "info",
                            message=stored_event.message,
                        )
                    )

            if stored_event.run_status is not None:
                run.status = stored_event.run_status
            elif run.status == InvestigationStatus.PENDING:
                run.status = InvestigationStatus.RUNNING

            if run.status in {InvestigationStatus.COMPLETED, InvestigationStatus.FAILED}:
                run.ended_at = stored_event.timestamp
                run.duration_ms = self._duration_ms(run.started_at, run.ended_at)
            else:
                run.ended_at = None
                run.duration_ms = None

            investigation = self.investigations.get(run.investigation_id)
            if investigation:
                self._apply_stage_output_to_investigation(investigation, stored_event)
                investigation.timeline.append(stored_event.message)
                investigation.updated_at = stored_event.timestamp
                investigation.status = run.status
                investigation.latest_run_status = run.status
                if run.status in {InvestigationStatus.COMPLETED, InvestigationStatus.FAILED}:
                    if investigation.active_run_id == run_id:
                        investigation.active_run_id = None
                else:
                    investigation.active_run_id = run_id

            return stored_event

    @staticmethod
    def _apply_stage_output_to_investigation(
        investigation: InvestigationRecord,
        event: WorkflowRunEvent,
    ) -> None:
        if event.stage_status != StepExecutionStatus.COMPLETED or event.stage_id is None:
            return

        metadata = event.metadata if isinstance(event.metadata, dict) else {}

        try:
            if event.stage_id == WorkflowStageId.RESOLVE_SERVICE_IDENTITY:
                payload = metadata.get("service_identity")
                if isinstance(payload, dict):
                    investigation.service_identity = ServiceIdentity.model_validate(payload)
                return

            if event.stage_id == WorkflowStageId.BUILD_INVESTIGATION_PLAN:
                payload = metadata.get("plan")
                if isinstance(payload, dict):
                    investigation.plan = InvestigationPlan.model_validate(payload)
                return

            if event.stage_id == WorkflowStageId.COLLECT_EVIDENCE:
                payload = metadata.get("evidence")
                if isinstance(payload, list):
                    investigation.evidence = [EvidenceItem.model_validate(item) for item in payload if isinstance(item, dict)]
                return

            if event.stage_id == WorkflowStageId.SYNTHESIZE_RCA_REPORT:
                report_payload = metadata.get("report")
                if isinstance(report_payload, dict):
                    investigation.report = RcaReport.model_validate(report_payload)

                hypotheses_payload = metadata.get("hypotheses")
                if isinstance(hypotheses_payload, list):
                    investigation.hypotheses = [
                        Hypothesis.model_validate(item) for item in hypotheses_payload if isinstance(item, dict)
                    ]
        except Exception:
            return

    def list_run_events(self, run_id: str, cursor: int = 0) -> list[WorkflowRunEvent]:
        events = self.run_events.get(run_id, [])
        if cursor < 0:
            cursor = 0
        return [event for event in events if (event.event_index or 0) > cursor]

    def complete_run(self, run_id: str, message: str = "Workflow completed") -> WorkflowRunEvent | None:
        run = self.run_details.get(run_id)
        if not run:
            return None

        event = WorkflowRunEvent(
            run_id=run.run_id,
            investigation_id=run.investigation_id,
            workflow_id=run.workflow_id,
            stage_id=None,
            stage_status=StepExecutionStatus.COMPLETED,
            run_status=InvestigationStatus.COMPLETED,
            timestamp=datetime.now(timezone.utc),
            message=message,
        )
        return self.append_run_event(run_id, event)

    def fail_run(self, run_id: str, message: str) -> WorkflowRunEvent | None:
        run = self.run_details.get(run_id)
        if not run:
            return None

        event = WorkflowRunEvent(
            run_id=run.run_id,
            investigation_id=run.investigation_id,
            workflow_id=run.workflow_id,
            stage_id=None,
            stage_status=StepExecutionStatus.FAILED,
            run_status=InvestigationStatus.FAILED,
            timestamp=datetime.now(timezone.utc),
            message=message,
            error=message,
        )
        return self.append_run_event(run_id, event)

    def upsert_connector_credential(
        self,
        provider: str,
        tenant: str,
        environment: str,
        mode: ConnectorCredentialMode,
        updated_by: str,
        secret_ref_name: str | None = None,
        secret_ref_key: str | None = None,
        key_last4: str | None = None,
    ) -> ConnectorCredentialView:
        credential = ConnectorCredentialView(
            provider=provider,
            tenant=tenant,
            environment=environment,
            mode=mode,
            secret_ref_name=secret_ref_name,
            secret_ref_key=secret_ref_key,
            key_last4=key_last4,
            updated_at=datetime.now(timezone.utc),
            updated_by=updated_by,
        )
        key = (tenant, environment, provider)
        self.connector_credentials[key] = credential
        self._persist_state()
        return credential

    def upsert_mapping(self, mapping: MappingUpsertRequest) -> MappingUpsertRequest:
        key = (mapping.provider, mapping.provider_entity_id)
        self.mappings[key] = mapping
        self._persist_state()
        return mapping

    def upsert_llm_route(self, route: LlmProviderRoute) -> LlmProviderRoute:
        self.llm_routes[(route.tenant, route.environment)] = route
        self._persist_state()
        return route

    def get_connector_credential(
        self,
        provider: str,
        tenant: str,
        environment: str,
    ) -> ConnectorCredentialView | None:
        return self.connector_credentials.get((tenant, environment, provider))

    def list_connector_credentials(
        self,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> list[ConnectorCredentialView]:
        creds = list(self.connector_credentials.values())
        if tenant:
            creds = [credential for credential in creds if credential.tenant == tenant]
        if environment:
            creds = [credential for credential in creds if credential.environment == environment]
        return sorted(creds, key=lambda credential: credential.updated_at, reverse=True)

    def get_llm_route(self, tenant: str, environment: str) -> LlmProviderRoute:
        route = self.llm_routes.get((tenant, environment))
        if route:
            return route
        return LlmProviderRoute(
            tenant=tenant,
            environment=environment,
            primary_model="codex",
            fallback_model="claude",
            key_ref="llm-provider-secret",
        )

    def upsert_mcp_server(self, config: McpServerConfig) -> McpServerConfig:
        key = (config.tenant, config.environment, config.server_id)
        self.mcp_servers[key] = config
        self._persist_state()
        return config

    def list_mcp_servers(self, tenant: str, environment: str | None = None) -> list[McpServerConfig]:
        servers = [item for item in self.mcp_servers.values() if item.tenant == tenant]
        if environment:
            servers = [item for item in servers if item.environment == environment]
        return sorted(servers, key=lambda item: item.updated_at, reverse=True)

    def get_mcp_server(self, tenant: str, environment: str, server_id: str) -> McpServerConfig | None:
        return self.mcp_servers.get((tenant, environment, server_id))

    def set_mcp_tools(
        self,
        tenant: str,
        environment: str,
        server_id: str,
        tools: list[McpToolDescriptor],
    ) -> None:
        self.mcp_tools[(tenant, environment, server_id)] = enrich_tool_descriptors(tools)
        self._persist_state()

    def get_mcp_tools(self, tenant: str, environment: str, server_id: str) -> list[McpToolDescriptor]:
        return enrich_tool_descriptors(self.mcp_tools.get((tenant, environment, server_id), []))

    def list_all_mcp_tools(self, tenant: str, environment: str) -> list[McpToolDescriptor]:
        tools: list[McpToolDescriptor] = []
        for (tool_tenant, tool_env, _), entries in self.mcp_tools.items():
            if tool_tenant == tenant and tool_env == environment:
                tools.extend(entries)
        return enrich_tool_descriptors(tools)

    def upsert_investigation_team(self, profile: InvestigationTeamProfile) -> InvestigationTeamProfile:
        key = (profile.tenant, profile.environment, profile.team_id)
        self.investigation_teams[key] = profile
        self._persist_state()
        return profile

    def get_investigation_team(
        self,
        tenant: str,
        environment: str,
        team_id: str,
    ) -> InvestigationTeamProfile | None:
        return self.investigation_teams.get((tenant, environment, team_id))

    def list_investigation_teams(
        self,
        tenant: str,
        environment: str | None = None,
    ) -> list[InvestigationTeamProfile]:
        profiles = [item for item in self.investigation_teams.values() if item.tenant == tenant]
        if environment:
            profiles = [item for item in profiles if item.environment == environment]
        return sorted(profiles, key=lambda item: (item.environment, item.team_id))

    def upsert_stage_mission(self, profile: StageMissionProfile) -> StageMissionProfile:
        key = (profile.tenant, profile.environment, profile.stage_id)
        self.stage_missions[key] = profile
        self._persist_state()
        return profile

    def get_stage_mission(
        self,
        tenant: str,
        environment: str,
        stage_id: WorkflowStageId,
    ) -> StageMissionProfile | None:
        key = (tenant, environment, stage_id)
        profile = self.stage_missions.get(key)
        if not profile:
            return None
        reconciled = self._reconcile_stage_mission(profile)
        if reconciled != profile:
            self.stage_missions[key] = reconciled
            self._persist_state()
        return reconciled

    def list_stage_missions(
        self,
        tenant: str,
        environment: str | None = None,
    ) -> list[StageMissionProfile]:
        profiles = [self.get_stage_mission(item.tenant, item.environment, item.stage_id) for item in self.stage_missions.values() if item.tenant == tenant]
        profiles = [item for item in profiles if item is not None]
        if environment:
            profiles = [item for item in profiles if item.environment == environment]
        return sorted(profiles, key=lambda item: (item.environment, item.stage_id.value))

    def upsert_team_mission(self, profile: TeamMissionProfile) -> TeamMissionProfile:
        key = (profile.tenant, profile.environment, profile.team_id)
        self.team_missions[key] = profile
        self._persist_state()
        return profile

    def get_team_mission(
        self,
        tenant: str,
        environment: str,
        team_id: str,
    ) -> TeamMissionProfile | None:
        key = (tenant, environment, team_id)
        profile = self.team_missions.get(key)
        if not profile:
            return None
        reconciled = self._reconcile_team_mission(profile)
        if reconciled != profile:
            self.team_missions[key] = reconciled
            self._persist_state()
        return reconciled

    def list_team_missions(
        self,
        tenant: str,
        environment: str | None = None,
    ) -> list[TeamMissionProfile]:
        profiles = [self.get_team_mission(item.tenant, item.environment, item.team_id) for item in self.team_missions.values() if item.tenant == tenant]
        profiles = [item for item in profiles if item is not None]
        if environment:
            profiles = [item for item in profiles if item.environment == environment]
        return sorted(profiles, key=lambda item: (item.environment, item.team_id))

    def _context_pack_key(self, tenant: str, environment: str, pack_id: str) -> tuple[str, str, str]:
        return (tenant, environment, pack_id)

    def list_context_packs(self, tenant: str, environment: str | None = None) -> list[ContextPack]:
        packs: list[ContextPack] = []
        for (pack_tenant, pack_environment, _), versions in self.context_pack_versions.items():
            if pack_tenant != tenant:
                continue
            if environment and pack_environment != environment:
                continue
            if not versions:
                continue
            packs.append(versions[-1])
        return sorted(packs, key=lambda item: item.updated_at, reverse=True)

    def get_context_pack(
        self,
        tenant: str,
        environment: str,
        pack_id: str,
        version: int | None = None,
    ) -> ContextPack | None:
        versions = self.context_pack_versions.get(self._context_pack_key(tenant, environment, pack_id), [])
        if not versions:
            return None
        if version is None:
            return versions[-1]
        for item in versions:
            if item.version == version:
                return item
        return None

    def create_context_pack(
        self,
        *,
        tenant: str,
        environment: str,
        pack_id: str,
        name: str,
        updated_by: str,
        description: str | None = None,
        stage_bindings: list[WorkflowStageId] | None = None,
        team_bindings: list[str] | None = None,
        service_tags: list[str] | None = None,
        infra_components: list[str] | None = None,
        dependencies: list[str] | None = None,
        validity_start: datetime | None = None,
        validity_end: datetime | None = None,
    ) -> ContextPack:
        now = datetime.now(timezone.utc)
        key = self._context_pack_key(tenant, environment, pack_id)
        versions = self.context_pack_versions[key]
        version = (versions[-1].version + 1) if versions else 1
        pack = ContextPack(
            pack_id=pack_id,
            tenant=tenant,
            environment=environment,
            name=name,
            description=description,
            version=version,
            status="draft",
            stage_bindings=stage_bindings or [],
            team_bindings=team_bindings or [],
            service_tags=service_tags or [],
            infra_components=infra_components or [],
            dependencies=dependencies or [],
            validity_start=validity_start,
            validity_end=validity_end,
            artifacts=[],
            active=False,
            created_at=now,
            updated_at=now,
            updated_by=updated_by,
        )
        versions.append(pack)
        self._persist_state()
        return pack

    def add_context_artifact(
        self,
        *,
        tenant: str,
        environment: str,
        pack_id: str,
        filename: str,
        artifact_type: str,
        content: str,
        updated_by: str,
        media_type: str | None = None,
        operator_notes: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ContextPack:
        current = self.get_context_pack(tenant, environment, pack_id)
        if not current:
            raise KeyError("context pack not found")

        chunks = self._chunk_text(content)
        parsed_chunks = [
            ContextChunk(
                chunk_id=f"chunk-{idx + 1}",
                text=text,
                metadata={"filename": filename, "artifact_type": artifact_type},
            )
            for idx, text in enumerate(chunks)
        ]
        now = datetime.now(timezone.utc)
        artifact = ContextArtifact(
            artifact_id=f"artifact-{uuid4()}",
            pack_id=pack_id,
            filename=filename,
            artifact_type=artifact_type,
            media_type=media_type,
            content=content,
            operator_notes=operator_notes,
            metadata=dict(metadata or {}),
            parsed_chunks=parsed_chunks,
            created_at=now,
            created_by=updated_by,
        )

        versions = self.context_pack_versions[self._context_pack_key(tenant, environment, pack_id)]
        next_version = current.version + 1
        cloned = current.model_copy(
            update={
                "version": next_version,
                "artifacts": [*current.artifacts, artifact],
                "updated_at": now,
                "updated_by": updated_by,
            },
            deep=True,
        )
        versions.append(cloned)
        active_key = (tenant, environment)
        if self.active_context_pack.get(active_key) == (pack_id, current.version):
            self.active_context_pack[active_key] = (pack_id, next_version)
            cloned.active = True
            current.active = False
        self._persist_state()
        return cloned

    def activate_context_pack(
        self,
        *,
        tenant: str,
        environment: str,
        pack_id: str,
        version: int | None = None,
    ) -> ContextPack:
        pack = self.get_context_pack(tenant, environment, pack_id, version=version)
        if not pack:
            raise KeyError("context pack not found")

        active_key = (tenant, environment)
        old = self.active_context_pack.get(active_key)
        if old:
            old_pack = self.get_context_pack(tenant, environment, old[0], version=old[1])
            if old_pack:
                old_pack.active = False

        pack.active = True
        pack.status = "active"
        self.active_context_pack[active_key] = (pack.pack_id, pack.version)
        self._persist_state()
        return pack

    def get_active_context_pack(self, tenant: str, environment: str) -> ContextPack | None:
        active = self.active_context_pack.get((tenant, environment))
        if not active:
            return None
        return self.get_context_pack(tenant, environment, active[0], version=active[1])

    def retrieve_context_refs(
        self,
        *,
        tenant: str,
        environment: str,
        stage_id: WorkflowStageId | None,
        team_id: str | None = None,
        alert_payload: dict[str, object] | None = None,
        limit: int = 8,
    ) -> list[ContextReference]:
        pack = self.get_active_context_pack(tenant, environment)
        if not pack:
            return []
        if pack.stage_bindings and stage_id and stage_id not in pack.stage_bindings:
            return []
        if pack.team_bindings and team_id and team_id not in pack.team_bindings:
            return []

        terms = self._context_terms_from_alert(alert_payload)
        terms.update({item.lower() for item in pack.service_tags if item.strip()})
        terms.update({item.lower() for item in pack.infra_components if item.strip()})

        refs: list[ContextReference] = []
        for artifact in pack.artifacts:
            for chunk in artifact.parsed_chunks:
                score = self._context_score(chunk.text, terms, stage_id, team_id)
                if score <= 0:
                    continue
                summary = chunk.text.replace("\n", " ").strip()
                if len(summary) > 180:
                    summary = summary[:177] + "..."
                refs.append(
                    ContextReference(
                        context_citation_id=f"CTX-{pack.pack_id}-v{pack.version}-{artifact.artifact_id}-{chunk.chunk_id}",
                        pack_id=pack.pack_id,
                        pack_version=pack.version,
                        artifact_id=artifact.artifact_id,
                        chunk_id=chunk.chunk_id,
                        stage_id=stage_id,
                        team_id=team_id,
                        summary=summary or artifact.filename,
                        score=score,
                    )
                )
        refs.sort(key=lambda item: item.score, reverse=True)
        return refs[: max(1, limit)]

    def upsert_agent_prompt_profile(self, profile: AgentPromptProfile) -> AgentPromptProfile:
        key = (profile.tenant, profile.environment, profile.stage_id)
        self.agent_prompt_profiles[key] = profile
        self._persist_state()
        return profile

    def list_agent_prompt_profiles(self, tenant: str, environment: str | None = None) -> list[AgentPromptProfile]:
        profiles = [
            self.get_agent_prompt_profile(item.tenant, item.environment, item.stage_id)
            for item in self.agent_prompt_profiles.values()
            if item.tenant == tenant
        ]
        profiles = [item for item in profiles if item is not None]
        if environment:
            profiles = [item for item in profiles if item.environment == environment]
        return sorted(profiles, key=lambda item: item.updated_at, reverse=True)

    def get_agent_prompt_profile(
        self,
        tenant: str,
        environment: str,
        stage_id: WorkflowStageId,
    ) -> AgentPromptProfile | None:
        key = (tenant, environment, stage_id)
        profile = self.agent_prompt_profiles.get(key)
        if not profile:
            return None
        reconciled = self._reconcile_agent_prompt_profile(profile)
        if reconciled != profile:
            self.agent_prompt_profiles[key] = reconciled
            self._persist_state()
        return reconciled

    def get_agent_rollout(self, tenant: str, environment: str) -> AgentRolloutConfig:
        rollout = self.agent_rollout_configs.get((tenant, environment))
        if rollout:
            return rollout
        return AgentRolloutConfig(
            tenant=tenant,
            environment=environment,
            mode=AgentRolloutMode.COMPARE,
            updated_at=datetime.now(timezone.utc),
            updated_by="system",
        )

    def upsert_agent_rollout(self, rollout: AgentRolloutConfig) -> AgentRolloutConfig:
        self.agent_rollout_configs[(rollout.tenant, rollout.environment)] = rollout
        self._persist_state()
        return rollout

    def get_workflow_layout(self, tenant: str, user_id: str, workflow_key: str) -> WorkflowLayoutState | None:
        return self.workflow_layouts.get((tenant, user_id, workflow_key))

    def upsert_workflow_layout(self, layout: WorkflowLayoutState) -> WorkflowLayoutState:
        key = (layout.tenant, layout.user_id, layout.workflow_key)
        self.workflow_layouts[key] = layout
        self._persist_state()
        return layout


store = InMemoryStore()
