from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import datetime, timezone
from hashlib import sha256
from os import getenv
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from platform_core.mcp_client import discover_mcp_tools, test_mcp_server
from platform_core.llm_router import ModelRoute, resolve_model_route
from platform_core.mcp_planning import build_mcp_only_plan, derive_argument_context
from platform_core.models import (
    AgentPromptProfile,
    AgentPromptProfileUpsertRequest,
    AgentRolloutConfig,
    AgentRolloutMode,
    AlertEnvelope,
    ConnectionTestResult,
    ConnectorCredentialMode,
    ConnectorCredentialUpsertRequest,
    ConnectorCredentialView,
    ContextArtifactUploadRequest,
    ContextPack,
    ContextPackActivateRequest,
    ContextPackCreateRequest,
    InvestigationRecord,
    InvestigationTeamProfile,
    InvestigationTeamProfileUpsertRequest,
    InvestigationStatus,
    LlmProviderRoute,
    McpServerConfig,
    McpServerUpsertRequest,
    McpToolDescriptor,
    MappingUpsertRequest,
    StageMissionProfile,
    StageMissionProfileUpsertRequest,
    StepExecutionStatus,
    TeamMissionProfile,
    TeamMissionProfileUpsertRequest,
    UserContext,
    WorkflowLayoutState,
    WorkflowLayoutUpsertRequest,
    WorkflowRunDetail,
    WorkflowRunEvent,
    WorkflowStageId,
)
from platform_core.policy import enforce_budget_policy
from platform_core.store import store

_TEMPORAL_CLIENT_SPEC = importlib.util.spec_from_file_location(
    "ingest_temporal_client",
    Path(__file__).with_name("temporal_client.py"),
)
if _TEMPORAL_CLIENT_SPEC is None or _TEMPORAL_CLIENT_SPEC.loader is None:
    raise RuntimeError("Unable to load temporal client module")
_TEMPORAL_CLIENT_MODULE = importlib.util.module_from_spec(_TEMPORAL_CLIENT_SPEC)
_TEMPORAL_CLIENT_SPEC.loader.exec_module(_TEMPORAL_CLIENT_MODULE)
start_investigation_workflow = _TEMPORAL_CLIENT_MODULE.start_investigation_workflow

app = FastAPI(title="rca-ingest-api", version="0.1.0")
allowed_origins = getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in allowed_origins if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class NewRelicWebhook(BaseModel):
    policy_name: str
    condition_name: str
    incident_id: str
    severity: str = "critical"
    entities: list[str] = []
    timestamp: datetime
    payload: dict = {}


def _coerce_grafana_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            if value > 1_000_000_000_000:
                return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _grafana_payload_to_envelope(payload: dict[str, Any]) -> AlertEnvelope:
    alerts_raw = payload.get("alerts")
    alerts = [item for item in alerts_raw if isinstance(item, dict)] if isinstance(alerts_raw, list) else []
    first_alert = alerts[0] if alerts else {}

    status = str(payload.get("status") or first_alert.get("status") or "").strip().lower()
    labels = first_alert.get("labels") if isinstance(first_alert.get("labels"), dict) else {}
    annotations = first_alert.get("annotations") if isinstance(first_alert.get("annotations"), dict) else {}

    severity = (
        labels.get("severity")
        or labels.get("alert_severity")
        or labels.get("level")
        or payload.get("severity")
        or payload.get("state")
    )
    if isinstance(severity, str):
        severity = severity.strip().lower()
    else:
        severity = ""
    if not severity:
        severity = "critical" if status == "firing" else "info"

    incident_key = str(payload.get("groupKey") or payload.get("group_key") or "").strip()
    if not incident_key:
        incident_key = str(first_alert.get("fingerprint") or "").strip()
    if not incident_key:
        seed = {
            "receiver": payload.get("receiver"),
            "status": status,
            "labels": labels,
            "startsAt": first_alert.get("startsAt"),
        }
        incident_key = f"grafana-{sha256(json.dumps(seed, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:16]}"

    entity_candidates: list[str] = []
    entity_keys = (
        "service",
        "service_name",
        "job",
        "app",
        "namespace",
        "pod",
        "instance",
        "container",
        "deployment",
        "alertname",
    )
    for alert in alerts:
        alert_labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
        for key in entity_keys:
            value = alert_labels.get(key)
            if isinstance(value, str) and value.strip():
                entity_candidates.append(value.strip())
    if not entity_candidates and isinstance(payload.get("receiver"), str):
        entity_candidates.append(payload["receiver"].strip())
    entity_ids = list(dict.fromkeys([item for item in entity_candidates if item]))[:24]

    start_candidates = [_coerce_grafana_timestamp(alert.get("startsAt")) for alert in alerts]
    end_candidates = [_coerce_grafana_timestamp(alert.get("endsAt")) for alert in alerts]
    start_candidates = [item for item in start_candidates if item]
    end_candidates = [item for item in end_candidates if item]
    triggered_at = min(start_candidates) if start_candidates else datetime.now(timezone.utc)
    updated_at = max([*start_candidates, *end_candidates]) if [*start_candidates, *end_candidates] else triggered_at

    raw_ref = (
        payload.get("externalURL")
        or payload.get("externalUrl")
        or first_alert.get("generatorURL")
        or first_alert.get("dashboardURL")
        or f"grafana://{incident_key}"
    )

    normalized_payload = {
        **payload,
        "receiver": payload.get("receiver"),
        "status": status,
        "title": payload.get("title") or annotations.get("summary") or labels.get("alertname"),
    }

    return AlertEnvelope(
        source="grafana",
        severity=severity,
        incident_key=incident_key,
        entity_ids=entity_ids,
        timestamps={"triggered_at": triggered_at, "updated_at": updated_at},
        raw_payload_ref=str(raw_ref) if raw_ref else f"grafana://{incident_key}",
        raw_payload=normalized_payload,
    )


class RunStartRequest(BaseModel):
    publish_outputs: bool = True


class AlertIngestResponse(BaseModel):
    investigation_id: str
    run_id: str | None = None
    workflow_id: str | None = None
    deduped: bool
    temporal_started: bool = False
    temporal_error: str | None = None


class RunStartResponse(BaseModel):
    investigation_id: str
    run_id: str
    workflow_id: str | None = None
    status: InvestigationStatus
    temporal_started: bool
    temporal_error: str | None = None


class RunListResponse(BaseModel):
    items: list[WorkflowRunDetail] = Field(default_factory=list)


class RunEventAck(BaseModel):
    status: str
    event_index: int


class AgentRolloutUpsertRequest(BaseModel):
    tenant: str = "default"
    environment: str = "prod"
    mode: AgentRolloutMode = AgentRolloutMode.COMPARE


# Ensure forward references from postponed annotations resolve under dynamic module loading in tests.
for _model in (
    NewRelicWebhook,
    RunStartRequest,
    AlertIngestResponse,
    RunStartResponse,
    RunListResponse,
    RunEventAck,
    AgentRolloutUpsertRequest,
):
    _model.model_rebuild(_types_namespace=globals())


def require_api_key(
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None, alias="apiKey"),
) -> None:
    configured = getenv("API_KEY")
    supplied = x_api_key or api_key
    if configured and supplied != configured:
        raise HTTPException(status_code=401, detail="invalid api key")


def require_internal_token(x_internal_token: str | None = Header(default=None)) -> None:
    configured = getenv("ORCHESTRATOR_EVENT_TOKEN")
    if configured and x_internal_token != configured:
        raise HTTPException(status_code=401, detail="invalid internal token")


def get_user_context(
    x_user_id: str | None = Header(default=None),
    x_user_role: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> UserContext:
    return UserContext(
        user_id=x_user_id or "local-user",
        role=(x_user_role or "admin").lower(),
        tenant=x_tenant_id or "default",
    )


def require_admin(user: UserContext = Depends(get_user_context)) -> UserContext:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return user


def _default_prompt_profile(
    tenant: str,
    environment: str,
    stage_id: WorkflowStageId,
    updated_by: str,
) -> AgentPromptProfile:
    if stage_id == WorkflowStageId.RESOLVE_SERVICE_IDENTITY:
        return AgentPromptProfile(
            tenant=tenant,
            environment=environment,
            stage_id=stage_id,
            system_prompt=(
                "You are an RCA resolver agent. You have read-only tools and must map alert entities "
                "to canonical service identity with confidence and ambiguity."
            ),
            objective_template=(
                "For incident {{incident_key}}, resolve canonical service, owner, environment, "
                "and ambiguous candidates."
            ),
            max_turns=4,
            max_tool_calls=6,
            tool_allowlist=["mcp.grafana.*", "mcp.jaeger.*", "mcp.prometheus.*"],
            updated_at=datetime.now(timezone.utc),
            updated_by=updated_by,
        )

    return AgentPromptProfile(
        tenant=tenant,
        environment=environment,
        stage_id=stage_id,
        system_prompt=(
            "You are an RCA planning agent. You must generate a bounded investigation plan using "
            "read-only light probes only in this stage."
        ),
        objective_template=(
            "For incident {{incident_key}}, produce an ordered bounded plan with provider, capability, "
            "rationale, timeout, and budget alignment."
        ),
        max_turns=4,
        max_tool_calls=6,
        tool_allowlist=["mcp.grafana.*", "mcp.jaeger.*", "mcp.prometheus.*"],
        updated_at=datetime.now(timezone.utc),
        updated_by=updated_by,
    )


def _default_stage_mission(
    tenant: str,
    environment: str,
    stage_id: WorkflowStageId,
    updated_by: str,
) -> StageMissionProfile:
    now = datetime.now(timezone.utc)
    defaults: dict[WorkflowStageId, dict[str, Any]] = {
        WorkflowStageId.RESOLVE_SERVICE_IDENTITY: {
            "mission_objective": "Resolve canonical service identity from alert context and MCP discovery tools.",
            "required_checks": ["alert_entities_reviewed", "canonical_service_selected"],
            "allowed_tools": ["mcp.grafana.*", "mcp.jaeger.*", "mcp.prometheus.*"],
            "completion_criteria": ["confidence_reported"],
            "unknown_not_available_rules": ["missing_entity_context"],
            "relevance_weights": {"alert": 1.0, "context_pack": 0.7},
            "alias_priority_order": ["entity_ids", "explicit_service", "title", "summary"],
            "alias_min_confidence": 0.72,
            "summary_tiebreak_only": True,
        },
        WorkflowStageId.BUILD_INVESTIGATION_PLAN: {
            "mission_objective": "Build bounded MCP-only plan aligned to service scope and investigation budgets.",
            "required_checks": ["mcp_only_steps", "budget_limits_applied", "target_service_scoped"],
            "allowed_tools": ["mcp.grafana.*", "mcp.jaeger.*", "mcp.prometheus.*"],
            "completion_criteria": ["ordered_steps_present"],
            "unknown_not_available_rules": ["no_invocable_tools"],
            "relevance_weights": {"alert": 1.0, "context_pack": 0.7, "tool_catalog": 0.9},
            "alias_priority_order": ["entity_ids", "explicit_service", "title", "summary"],
            "alias_min_confidence": 0.72,
            "summary_tiebreak_only": True,
        },
        WorkflowStageId.COLLECT_EVIDENCE: {
            "mission_objective": "Collect read-only MCP evidence with team boundaries and citation coverage.",
            "required_checks": ["team_execution_completed", "citations_created"],
            "allowed_tools": ["mcp.grafana.*", "mcp.jaeger.*", "mcp.prometheus.*"],
            "completion_criteria": ["evidence_items_collected"],
            "unknown_not_available_rules": ["missing_required_signals"],
            "relevance_weights": {"service_scoped": 1.0, "global": 0.6},
        },
        WorkflowStageId.SYNTHESIZE_RCA_REPORT: {
            "mission_objective": "Arbitrate team mini RCAs and produce a final top-3 RCA with citations.",
            "required_checks": ["top3_generated", "citations_attached"],
            "allowed_tools": [],
            "completion_criteria": ["likely_cause_present", "manual_actions_present"],
            "unknown_not_available_rules": ["insufficient_citations"],
            "relevance_weights": {"team_reports": 1.0, "raw_evidence": 0.9},
        },
        WorkflowStageId.EMIT_EVAL_EVENT: {
            "mission_objective": "Emit eval and training artifacts for offline/online quality monitoring.",
            "required_checks": ["latency_emitted", "citation_metrics_emitted"],
            "allowed_tools": [],
            "completion_criteria": ["eval_payload_valid"],
            "unknown_not_available_rules": ["missing_eval_dimensions"],
            "relevance_weights": {"metrics": 1.0, "training_artifacts": 0.8},
        },
    }
    selected = defaults.get(
        stage_id,
        {
            "mission_objective": f"Execute {stage_id.value} mission safely with citations.",
            "required_checks": [],
            "allowed_tools": [],
            "completion_criteria": [],
            "unknown_not_available_rules": [],
            "relevance_weights": {},
            "alias_priority_order": [],
            "alias_min_confidence": 0.7,
            "summary_tiebreak_only": True,
        },
    )
    return StageMissionProfile(
        tenant=tenant,
        environment=environment,
        stage_id=stage_id,
        mission_objective=selected["mission_objective"],
        required_checks=selected["required_checks"],
        allowed_tools=selected["allowed_tools"],
        completion_criteria=selected["completion_criteria"],
        unknown_not_available_rules=selected["unknown_not_available_rules"],
        relevance_weights=selected["relevance_weights"],
        alias_priority_order=selected["alias_priority_order"],
        alias_min_confidence=selected["alias_min_confidence"],
        summary_tiebreak_only=selected["summary_tiebreak_only"],
        updated_at=now,
        updated_by=updated_by,
    )


def _default_team_mission(
    tenant: str,
    environment: str,
    team_id: str,
    updated_by: str,
) -> TeamMissionProfile:
    now = datetime.now(timezone.utc)
    defaults: dict[str, dict[str, Any]] = {
        "app": {
            "mission_objective": "Investigate application failures using Jaeger traces and service operations.",
            "required_checks": ["trace_errors_checked", "service_operations_reviewed"],
            "allowed_tools": ["mcp.jaeger.*"],
            "completion_criteria": ["mini_rca_produced"],
            "unknown_not_available_rules": ["no_trace_evidence"],
            "relevance_weights": {"service_scoped": 1.0, "global": 0.4},
        },
        "infra": {
            "mission_objective": "Investigate infrastructure saturation, platform anomalies, and shared dependencies.",
            "required_checks": ["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
            "allowed_tools": ["mcp.grafana.*", "mcp.prometheus.*"],
            "completion_criteria": ["infra_completeness_reported"],
            "unknown_not_available_rules": ["missing_required_checks", "missing_infra_signals"],
            "relevance_weights": {"service_scoped": 1.0, "global": 0.8},
            "evidence_requirements": [
                {
                    "evidence_class": "annotation_change_context",
                    "description": "Check deployment, annotation, or maintenance context around the incident window.",
                    "tool_patterns": ["mcp.grafana.get_annotations", "mcp.grafana.get_annotation_tags"],
                    "query_scope": "change",
                    "required_symptoms": [],
                },
                {
                    "evidence_class": "local_service_metrics",
                    "description": "Validate latency/error/throughput metrics for the resolved service.",
                    "tool_patterns": ["mcp.prometheus.query_range", "mcp.prometheus.query_instant"],
                    "query_scope": "service",
                    "required_symptoms": ["latency", "resource", "memory", "cpu", "error"],
                },
                {
                    "evidence_class": "global_shared_metrics",
                    "description": "Validate whether the anomaly is shared across the platform or isolated to the service.",
                    "tool_patterns": ["mcp.prometheus.query_range", "mcp.prometheus.query_instant"],
                    "query_scope": "global",
                    "required_symptoms": ["latency", "resource", "memory", "cpu", "error"],
                },
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
            "mission_objective": "Investigate datastore contention and query/connection health.",
            "required_checks": ["db_signal_checked"],
            "allowed_tools": [],
            "completion_criteria": ["db_or_unknown_reported"],
            "unknown_not_available_rules": ["no_db_tools_configured"],
            "relevance_weights": {"service_scoped": 1.0, "global": 0.6},
        },
    }
    selected = defaults.get(
        team_id,
        {
            "mission_objective": f"Investigate {team_id} domain and report with citations.",
            "required_checks": [],
            "allowed_tools": [],
            "completion_criteria": [],
            "unknown_not_available_rules": [],
            "relevance_weights": {},
            "evidence_requirements": [],
            "symptom_overrides": {},
        },
    )
    return TeamMissionProfile(
        team_id=team_id,
        tenant=tenant,
        environment=environment,
        mission_objective=selected["mission_objective"],
        required_checks=selected["required_checks"],
        allowed_tools=selected["allowed_tools"],
        completion_criteria=selected["completion_criteria"],
        unknown_not_available_rules=selected["unknown_not_available_rules"],
        relevance_weights=selected["relevance_weights"],
        evidence_requirements=selected["evidence_requirements"],
        symptom_overrides=selected["symptom_overrides"],
        updated_at=now,
        updated_by=updated_by,
    )


async def _start_investigation_run(
    investigation_id: str,
    *,
    publish_outputs: bool,
    started_by: str,
    tenant: str,
    environment: str = "prod",
) -> RunStartResponse:
    investigation = store.get_investigation(investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="investigation not found")

    run_id = f"run-{uuid4()}"
    workflow_id = f"rca-{investigation_id}-{run_id}"
    run = store.create_run(
        investigation_id=investigation_id,
        run_id=run_id,
        workflow_id=workflow_id,
        started_by=started_by,
    )
    if not run:
        raise HTTPException(status_code=404, detail="investigation not found")

    store.append_run_event(
        run_id,
        WorkflowRunEvent(
            run_id=run_id,
            investigation_id=investigation_id,
            workflow_id=workflow_id,
            stage_id=None,
            stage_status=StepExecutionStatus.QUEUED,
            run_status=InvestigationStatus.RUNNING,
            attempt=1,
            timestamp=datetime.now(timezone.utc),
            message="Workflow run queued",
            metadata={"started_by": started_by},
        ),
    )

    llm_route = store.get_llm_route(tenant=tenant, environment=environment)
    rollout = store.get_agent_rollout(tenant=tenant, environment=environment)
    prompt_profiles = {
        profile.stage_id.value: profile.model_dump(mode="json")
        for profile in store.list_agent_prompt_profiles(tenant=tenant, environment=environment)
    }
    mcp_servers = [item.model_dump(mode="json") for item in store.list_mcp_servers(tenant=tenant, environment=environment)]
    mcp_tools: list[dict] = []
    for mcp_server in store.list_mcp_servers(tenant=tenant, environment=environment):
        mcp_tools.extend([tool.model_dump(mode="json") for tool in store.get_mcp_tools(tenant, environment, mcp_server.server_id)])
    investigation_teams = [
        team.model_dump(mode="json")
        for team in store.list_investigation_teams(tenant=tenant, environment=environment)
    ]
    stage_missions = {
        profile.stage_id.value: profile.model_dump(mode="json")
        for profile in store.list_stage_missions(tenant=tenant, environment=environment)
    }
    if not stage_missions:
        defaults = [
            _default_stage_mission(tenant, environment, WorkflowStageId.RESOLVE_SERVICE_IDENTITY, "system"),
            _default_stage_mission(tenant, environment, WorkflowStageId.BUILD_INVESTIGATION_PLAN, "system"),
            _default_stage_mission(tenant, environment, WorkflowStageId.COLLECT_EVIDENCE, "system"),
            _default_stage_mission(tenant, environment, WorkflowStageId.SYNTHESIZE_RCA_REPORT, "system"),
            _default_stage_mission(tenant, environment, WorkflowStageId.EMIT_EVAL_EVENT, "system"),
        ]
        for profile in defaults:
            store.upsert_stage_mission(profile)
        stage_missions = {
            profile.stage_id.value: profile.model_dump(mode="json")
            for profile in store.list_stage_missions(tenant=tenant, environment=environment)
        }

    team_missions = {
        profile.team_id: profile.model_dump(mode="json")
        for profile in store.list_team_missions(tenant=tenant, environment=environment)
    }
    if not team_missions:
        defaults = [
            _default_team_mission(tenant, environment, "app", "system"),
            _default_team_mission(tenant, environment, "infra", "system"),
            _default_team_mission(tenant, environment, "db", "system"),
        ]
        for profile in defaults:
            store.upsert_team_mission(profile)
        team_missions = {
            profile.team_id: profile.model_dump(mode="json")
            for profile in store.list_team_missions(tenant=tenant, environment=environment)
        }

    active_context_pack = store.get_active_context_pack(tenant=tenant, environment=environment)

    started_workflow_id, temporal_error = await start_investigation_workflow(
        investigation_id=investigation_id,
        run_id=run_id,
        workflow_id=workflow_id,
        alert_payload=investigation.alert.model_dump(mode="json"),
        publish_outputs=publish_outputs,
        tenant=tenant,
        environment=environment,
        llm_route=llm_route.model_dump(mode="json"),
        agent_prompt_profiles=prompt_profiles,
        agent_rollout_mode=rollout.mode.value,
        mcp_servers=mcp_servers,
        mcp_tools=mcp_tools,
        investigation_teams=investigation_teams,
        stage_missions=stage_missions,
        team_missions=team_missions,
        active_context_pack=active_context_pack.model_dump(mode="json") if active_context_pack else None,
        execution_policy="mcp_only",
    )

    if started_workflow_id:
        store.set_run_workflow_id(run_id, started_workflow_id)
        store.append_run_event(
            run_id,
            WorkflowRunEvent(
                run_id=run_id,
                investigation_id=investigation_id,
                workflow_id=started_workflow_id,
                stage_id=None,
                stage_status=StepExecutionStatus.RUNNING,
                run_status=InvestigationStatus.RUNNING,
                attempt=1,
                timestamp=datetime.now(timezone.utc),
                message=f"Temporal workflow started ({started_workflow_id})",
            ),
        )
    else:
        store.fail_run(run_id, f"Unable to start temporal workflow: {temporal_error}")

    final_run = store.get_run(run_id)
    if not final_run:
        raise HTTPException(status_code=404, detail="run not found")

    return RunStartResponse(
        investigation_id=investigation_id,
        run_id=run_id,
        workflow_id=started_workflow_id,
        status=final_run.status,
        temporal_started=bool(started_workflow_id),
        temporal_error=temporal_error,
    )


@app.get("/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ingest-api"}


@app.get("/v1/metrics")
def metrics() -> dict[str, int]:
    return {"alerts_ingested_total": store.counters["alerts_ingested_total"]}


@app.get("/v1/me")
def whoami(
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> UserContext:
    return user


@app.post("/v1/alerts")
async def ingest_alert(
    alert: AlertEnvelope,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> AlertIngestResponse:
    investigation_id = str(uuid4())
    now = datetime.now(timezone.utc)
    mcp_tools = store.list_all_mcp_tools(tenant=user.tenant, environment="prod")
    context = derive_argument_context(alert.model_dump(mode="json"), {})
    default_allowlist = ["mcp.grafana.*", "mcp.jaeger.*", "mcp.prometheus.*"]
    plan, _ = build_mcp_only_plan(
        investigation_id=investigation_id,
        tools=mcp_tools,
        context=context,
        allowlist=default_allowlist,
        max_steps=6,
        max_api_calls=10,
        max_stage_wall_clock_seconds=600,
    )
    enforce_budget_policy(plan)

    record = InvestigationRecord(
        id=investigation_id,
        status=InvestigationStatus.PENDING,
        created_at=now,
        updated_at=now,
        alert=alert,
        plan=plan,
        timeline=["Alert accepted", "Investigation plan created"],
    )

    saved_id, deduped = store.record_alert(record)
    if deduped:
        existing = store.get_investigation(saved_id)
        active_run_id = existing.active_run_id if existing else None
        active_run = store.get_run(active_run_id) if active_run_id else None
        return AlertIngestResponse(
            investigation_id=saved_id,
            run_id=active_run_id,
            workflow_id=active_run.workflow_id if active_run else None,
            deduped=True,
            temporal_started=bool(active_run_id),
        )

    run_response = await _start_investigation_run(
        investigation_id,
        publish_outputs=True,
        started_by=user.user_id,
        tenant=user.tenant,
        environment="prod",
    )

    return AlertIngestResponse(
        investigation_id=investigation_id,
        run_id=run_response.run_id,
        workflow_id=run_response.workflow_id,
        deduped=False,
        temporal_started=run_response.temporal_started,
        temporal_error=run_response.temporal_error,
    )


@app.post("/v1/alerts/newrelic")
async def ingest_newrelic_alert(
    payload: NewRelicWebhook,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> AlertIngestResponse:
    envelope = AlertEnvelope(
        source="newrelic",
        severity=payload.severity,
        incident_key=payload.incident_id,
        entity_ids=payload.entities,
        timestamps={"triggered_at": payload.timestamp},
        raw_payload_ref=f"newrelic://{payload.incident_id}",
        raw_payload=payload.payload,
    )
    return await ingest_alert(envelope, _=None, user=user)


@app.post("/v1/alerts/grafana")
async def ingest_grafana_alert(
    payload: dict[str, Any],
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> AlertIngestResponse:
    envelope = _grafana_payload_to_envelope(payload)
    return await ingest_alert(envelope, _=None, user=user)


@app.get("/v1/investigations/{investigation_id}")
def get_investigation(investigation_id: str, _: None = Depends(require_api_key)) -> InvestigationRecord:
    investigation = store.get_investigation(investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="investigation not found")
    return investigation


@app.get("/v1/investigations")
def list_investigations(
    status: InvestigationStatus | None = None,
    source: str | None = None,
    severity: str | None = None,
    page: int = 1,
    page_size: int = 20,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> dict:
    page = max(page, 1)
    page_size = max(min(page_size, 100), 1)
    records, total = store.list_investigations(
        status=status,
        source=source,
        severity=severity,
        tenant=user.tenant,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [record.model_dump(mode="json") for record in records],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/v1/investigations/{investigation_id}/runs")
async def start_run(
    investigation_id: str,
    request: RunStartRequest,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> RunStartResponse:
    return await _start_investigation_run(
        investigation_id,
        publish_outputs=request.publish_outputs,
        started_by=user.user_id,
        tenant=user.tenant,
        environment="prod",
    )


@app.get("/v1/investigations/{investigation_id}/runs")
def list_runs(
    investigation_id: str,
    _: None = Depends(require_api_key),
) -> RunListResponse:
    if not store.get_investigation(investigation_id):
        raise HTTPException(status_code=404, detail="investigation not found")
    return RunListResponse(items=store.list_runs(investigation_id))


@app.get("/v1/investigations/{investigation_id}/runs/{run_id}")
def get_run(
    investigation_id: str,
    run_id: str,
    _: None = Depends(require_api_key),
) -> WorkflowRunDetail:
    run = store.get_run_for_investigation(investigation_id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@app.get("/v1/investigations/{investigation_id}/runs/{run_id}/events")
async def stream_run_events(
    investigation_id: str,
    run_id: str,
    cursor: int = 0,
    _: None = Depends(require_api_key),
) -> StreamingResponse:
    if not store.get_run_for_investigation(investigation_id, run_id):
        raise HTTPException(status_code=404, detail="run not found")

    async def event_generator() -> str:
        local_cursor = cursor
        while True:
            run = store.get_run_for_investigation(investigation_id, run_id)
            if not run:
                payload = {"investigation_id": investigation_id, "run_id": run_id, "message": "run removed"}
                yield f"event: error\ndata: {json.dumps(payload)}\n\n"
                break

            events = store.list_run_events(run_id, local_cursor)
            if events:
                for event in events:
                    local_cursor = event.event_index or local_cursor
                    payload = event.model_dump(mode="json")
                    yield f"event: run_event\ndata: {json.dumps(payload)}\n\n"
            else:
                heartbeat = {"ts": datetime.now(timezone.utc).isoformat(), "run_id": run_id}
                yield f"event: heartbeat\ndata: {json.dumps(heartbeat)}\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/v1/internal/runs/events")
def ingest_run_event(
    event: WorkflowRunEvent,
    _: None = Depends(require_internal_token),
) -> RunEventAck:
    run = store.get_run_for_investigation(event.investigation_id, event.run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")

    stored = store.append_run_event(event.run_id, event)
    if not stored or stored.event_index is None:
        raise HTTPException(status_code=404, detail="run not found")

    return RunEventAck(status="ok", event_index=stored.event_index)


@app.get("/v1/investigations/{investigation_id}/events")
async def stream_investigation_events(
    investigation_id: str,
    cursor: int = 0,
    _: None = Depends(require_api_key),
) -> StreamingResponse:
    investigation = store.get_investigation(investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="investigation not found")

    active_run = store.get_active_run(investigation_id)
    if active_run:
        run_id = active_run.run_id

        async def active_run_generator() -> str:
            local_cursor = cursor
            while True:
                run = store.get_run_for_investigation(investigation_id, run_id)
                if not run:
                    payload = {"investigation_id": investigation_id, "message": "run removed"}
                    yield f"event: error\ndata: {json.dumps(payload)}\n\n"
                    break

                events = store.list_run_events(run_id, local_cursor)
                if events:
                    for event in events:
                        local_cursor = event.event_index or local_cursor
                        payload = {
                            "event_index": event.event_index,
                            "investigation_id": investigation_id,
                            "run_id": run_id,
                            "workflow_id": event.workflow_id,
                            "status": (event.run_status or run.status).value,
                            "stage_status": event.stage_status.value,
                            "stage_id": event.stage_id.value if event.stage_id else None,
                            "message": event.message,
                            "updated_at": event.timestamp.isoformat(),
                        }
                        yield f"event: stage_update\ndata: {json.dumps(payload)}\n\n"
                else:
                    heartbeat = {"ts": datetime.now(timezone.utc).isoformat()}
                    yield f"event: heartbeat\ndata: {json.dumps(heartbeat)}\n\n"

                await asyncio.sleep(2)

        return StreamingResponse(active_run_generator(), media_type="text/event-stream")

    async def timeline_fallback_generator() -> str:
        local_cursor = cursor
        while True:
            updated = store.get_investigation(investigation_id)
            if not updated:
                payload = {"investigation_id": investigation_id, "message": "investigation removed"}
                yield f"event: error\ndata: {json.dumps(payload)}\n\n"
                break

            timeline = updated.timeline
            if local_cursor < len(timeline):
                new_events = timeline[local_cursor:]
                for idx, message in enumerate(new_events, start=1):
                    payload = {
                        "event_index": local_cursor + idx,
                        "investigation_id": investigation_id,
                        "status": updated.status.value,
                        "message": message,
                        "updated_at": updated.updated_at.isoformat(),
                    }
                    yield f"event: stage_update\ndata: {json.dumps(payload)}\n\n"
                local_cursor = len(timeline)
            else:
                heartbeat = {"ts": datetime.now(timezone.utc).isoformat()}
                yield f"event: heartbeat\ndata: {json.dumps(heartbeat)}\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(timeline_fallback_generator(), media_type="text/event-stream")


@app.post("/v1/investigations/{investigation_id}/rerun")
async def rerun_investigation(
    investigation_id: str,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> dict[str, str]:
    run = await _start_investigation_run(
        investigation_id,
        publish_outputs=True,
        started_by=user.user_id,
        tenant=user.tenant,
        environment="prod",
    )
    return {
        "investigation_id": run.investigation_id,
        "status": run.status.value,
        "run_id": run.run_id,
        "workflow_id": run.workflow_id or "",
    }


@app.post("/v1/catalog/mappings/upsert")
def upsert_mapping(mapping: MappingUpsertRequest, _: None = Depends(require_api_key)) -> dict[str, str]:
    store.upsert_mapping(mapping)
    return {"status": "ok", "canonical_service_id": mapping.canonical_service_id}


@app.post("/v1/providers/llm")
def upsert_llm_route(route: LlmProviderRoute, _: None = Depends(require_api_key)) -> dict[str, str]:
    store.upsert_llm_route(route)
    return {"status": "ok", "tenant": route.tenant, "environment": route.environment}


@app.get("/v1/settings/connectors")
def get_connector_settings(
    environment: str = "prod",
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> dict[str, list[ConnectorCredentialView]]:
    settings = store.list_connector_credentials(tenant=user.tenant, environment=environment)
    return {"items": settings}


@app.put("/v1/settings/connectors/{provider}")
def upsert_connector_settings(
    provider: str,
    request: ConnectorCredentialUpsertRequest,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(require_admin),
) -> ConnectorCredentialView:
    mode = request.mode
    if mode == ConnectorCredentialMode.SECRET_REF:
        if not request.secret_ref_name or not request.secret_ref_key:
            raise HTTPException(status_code=400, detail="secret_ref_name and secret_ref_key are required")
        credential = store.upsert_connector_credential(
            provider=provider,
            tenant=request.tenant or user.tenant,
            environment=request.environment,
            mode=mode,
            secret_ref_name=request.secret_ref_name,
            secret_ref_key=request.secret_ref_key,
            key_last4=None,
            updated_by=user.user_id,
        )
        return credential

    if not request.raw_key:
        raise HTTPException(status_code=400, detail="raw_key is required for raw_key mode")

    credential = store.upsert_connector_credential(
        provider=provider,
        tenant=request.tenant or user.tenant,
        environment=request.environment,
        mode=mode,
        secret_ref_name=None,
        secret_ref_key=None,
        key_last4=request.raw_key[-4:],
        updated_by=user.user_id,
    )
    return credential


@app.post("/v1/settings/connectors/{provider}/test")
def test_connector_settings(
    provider: str,
    environment: str = "prod",
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> ConnectionTestResult:
    credential = store.get_connector_credential(provider=provider, tenant=user.tenant, environment=environment)
    if not credential:
        return ConnectionTestResult(
            provider=provider,
            tenant=user.tenant,
            environment=environment,
            success=False,
            detail="No connector settings found for provider/environment.",
        )

    return ConnectionTestResult(
        provider=provider,
        tenant=user.tenant,
        environment=environment,
        success=True,
        detail=f"Connector configuration present (mode={credential.mode.value}).",
    )


@app.get("/v1/settings/llm-routes")
def list_llm_routes(
    environment: str | None = None,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> dict[str, list[LlmProviderRoute]]:
    routes = list(store.llm_routes.values())
    routes = [route for route in routes if route.tenant == user.tenant]
    if environment:
        routes = [route for route in routes if route.environment == environment]
    return {"items": routes}


@app.put("/v1/settings/llm-routes")
def upsert_llm_route_settings(
    route: LlmProviderRoute,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(require_admin),
) -> dict[str, str]:
    if route.tenant != user.tenant:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    try:
        resolve_model_route(
            ModelRoute(
                primary=route.primary_model,
                fallback=route.fallback_model,
                key_ref=route.key_ref,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid model route: {exc}") from exc
    store.upsert_llm_route(route)
    return {"status": "ok", "tenant": route.tenant, "environment": route.environment}


@app.get("/v1/settings/mcp-servers")
def list_mcp_servers(
    environment: str | None = None,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> dict[str, list[McpServerConfig]]:
    items = store.list_mcp_servers(tenant=user.tenant, environment=environment)
    return {"items": items}


@app.put("/v1/settings/mcp-servers/{server_id}")
def upsert_mcp_server_settings(
    server_id: str,
    request: McpServerUpsertRequest,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(require_admin),
) -> McpServerConfig:
    if request.tenant != user.tenant:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    config = McpServerConfig(
        server_id=server_id,
        tenant=request.tenant,
        environment=request.environment,
        transport=request.transport,
        base_url=request.base_url,
        secret_ref_name=request.secret_ref_name,
        secret_ref_key=request.secret_ref_key,
        timeout_seconds=request.timeout_seconds,
        enabled=request.enabled,
        updated_at=datetime.now(timezone.utc),
        updated_by=user.user_id,
    )
    store.upsert_mcp_server(config)
    if config.enabled:
        try:
            tools = discover_mcp_tools(config)
            store.set_mcp_tools(config.tenant, config.environment, config.server_id, tools)
        except Exception:
            store.set_mcp_tools(config.tenant, config.environment, config.server_id, [])
    return config


@app.post("/v1/settings/mcp-servers/{server_id}/test")
def test_mcp_server_settings(
    server_id: str,
    environment: str = "prod",
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> dict[str, Any]:
    config = store.get_mcp_server(user.tenant, environment, server_id)
    if not config:
        return {"success": False, "detail": "MCP server config not found."}
    success, detail = test_mcp_server(config)
    return {"success": success, "detail": detail}


@app.get("/v1/settings/mcp-servers/{server_id}/tools")
def list_mcp_server_tools(
    server_id: str,
    environment: str = "prod",
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> dict[str, list[McpToolDescriptor]]:
    config = store.get_mcp_server(user.tenant, environment, server_id)
    if not config:
        raise HTTPException(status_code=404, detail="mcp server not found")
    tools = store.get_mcp_tools(user.tenant, environment, server_id)
    if not tools and config.enabled:
        try:
            tools = discover_mcp_tools(config)
            store.set_mcp_tools(user.tenant, environment, server_id, tools)
        except Exception:
            tools = []
    return {"items": tools}


@app.get("/v1/settings/agent-prompts")
def list_agent_prompt_profiles(
    environment: str = "prod",
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> dict[str, list[AgentPromptProfile]]:
    profiles = store.list_agent_prompt_profiles(tenant=user.tenant, environment=environment)
    if not profiles:
        defaults = [
            _default_prompt_profile(user.tenant, environment, WorkflowStageId.RESOLVE_SERVICE_IDENTITY, "system"),
            _default_prompt_profile(user.tenant, environment, WorkflowStageId.BUILD_INVESTIGATION_PLAN, "system"),
        ]
        for profile in defaults:
            store.upsert_agent_prompt_profile(profile)
        profiles = store.list_agent_prompt_profiles(tenant=user.tenant, environment=environment)
    return {"items": profiles}


@app.put("/v1/settings/agent-prompts/{stage_id}")
def upsert_agent_prompt_profile(
    stage_id: WorkflowStageId,
    request: AgentPromptProfileUpsertRequest,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(require_admin),
) -> AgentPromptProfile:
    if request.tenant != user.tenant:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    profile = AgentPromptProfile(
        tenant=request.tenant,
        environment=request.environment,
        stage_id=stage_id,
        system_prompt=request.system_prompt,
        objective_template=request.objective_template,
        max_turns=request.max_turns,
        max_tool_calls=request.max_tool_calls,
        tool_allowlist=request.tool_allowlist,
        updated_at=datetime.now(timezone.utc),
        updated_by=user.user_id,
    )
    return store.upsert_agent_prompt_profile(profile)


@app.get("/v1/settings/agent-rollout")
def get_agent_rollout(
    environment: str = "prod",
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> AgentRolloutConfig:
    return store.get_agent_rollout(tenant=user.tenant, environment=environment)


@app.put("/v1/settings/agent-rollout")
def upsert_agent_rollout(
    request: AgentRolloutUpsertRequest,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(require_admin),
) -> AgentRolloutConfig:
    if request.tenant != user.tenant:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    rollout = AgentRolloutConfig(
        tenant=request.tenant,
        environment=request.environment,
        mode=request.mode,
        updated_at=datetime.now(timezone.utc),
        updated_by=user.user_id,
    )
    return store.upsert_agent_rollout(rollout)


@app.get("/v1/settings/investigation-teams")
def list_investigation_teams(
    environment: str = "prod",
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> dict[str, list[InvestigationTeamProfile]]:
    teams = store.list_investigation_teams(tenant=user.tenant, environment=environment)
    return {"items": teams}


@app.put("/v1/settings/investigation-teams/{team_id}")
def upsert_investigation_team(
    team_id: str,
    request: InvestigationTeamProfileUpsertRequest,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(require_admin),
) -> InvestigationTeamProfile:
    if request.tenant != user.tenant:
        raise HTTPException(status_code=403, detail="tenant mismatch")

    profile = InvestigationTeamProfile(
        team_id=team_id,
        tenant=request.tenant,
        environment=request.environment,
        enabled=request.enabled,
        objective_prompt=request.objective_prompt,
        tool_allowlist=request.tool_allowlist,
        max_tool_calls=request.max_tool_calls,
        max_parallel_calls=request.max_parallel_calls,
        timeout_seconds=request.timeout_seconds,
        updated_at=datetime.now(timezone.utc),
        updated_by=user.user_id,
    )
    return store.upsert_investigation_team(profile)


@app.get("/v1/settings/stage-missions/{stage_id}")
def get_stage_mission(
    stage_id: WorkflowStageId,
    environment: str = "prod",
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> StageMissionProfile:
    mission = store.get_stage_mission(user.tenant, environment, stage_id)
    if mission:
        return mission
    default = _default_stage_mission(user.tenant, environment, stage_id, "system")
    return store.upsert_stage_mission(default)


@app.put("/v1/settings/stage-missions/{stage_id}")
def upsert_stage_mission(
    stage_id: WorkflowStageId,
    request: StageMissionProfileUpsertRequest,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(require_admin),
) -> StageMissionProfile:
    if request.tenant != user.tenant:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    mission = StageMissionProfile(
        tenant=request.tenant,
        environment=request.environment,
        stage_id=stage_id,
        mission_objective=request.mission_objective,
        required_checks=request.required_checks,
        allowed_tools=request.allowed_tools,
        completion_criteria=request.completion_criteria,
        unknown_not_available_rules=request.unknown_not_available_rules,
        relevance_weights=request.relevance_weights,
        alias_priority_order=request.alias_priority_order,
        alias_min_confidence=request.alias_min_confidence,
        summary_tiebreak_only=request.summary_tiebreak_only,
        updated_at=datetime.now(timezone.utc),
        updated_by=user.user_id,
    )
    return store.upsert_stage_mission(mission)


@app.get("/v1/settings/team-missions/{team_id}")
def get_team_mission(
    team_id: str,
    environment: str = "prod",
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> TeamMissionProfile:
    mission = store.get_team_mission(user.tenant, environment, team_id)
    if mission:
        return mission
    default = _default_team_mission(user.tenant, environment, team_id, "system")
    return store.upsert_team_mission(default)


@app.put("/v1/settings/team-missions/{team_id}")
def upsert_team_mission(
    team_id: str,
    request: TeamMissionProfileUpsertRequest,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(require_admin),
) -> TeamMissionProfile:
    if request.tenant != user.tenant:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    mission = TeamMissionProfile(
        team_id=team_id,
        tenant=request.tenant,
        environment=request.environment,
        mission_objective=request.mission_objective,
        required_checks=request.required_checks,
        allowed_tools=request.allowed_tools,
        completion_criteria=request.completion_criteria,
        unknown_not_available_rules=request.unknown_not_available_rules,
        relevance_weights=request.relevance_weights,
        evidence_requirements=request.evidence_requirements,
        symptom_overrides=request.symptom_overrides,
        updated_at=datetime.now(timezone.utc),
        updated_by=user.user_id,
    )
    return store.upsert_team_mission(mission)


@app.get("/v1/settings/context-packs")
def list_context_packs(
    environment: str = "prod",
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> dict[str, Any]:
    packs = store.list_context_packs(user.tenant, environment=environment)
    active = store.get_active_context_pack(user.tenant, environment=environment)
    return {
        "items": [pack.model_dump(mode="json") for pack in packs],
        "active": active.model_dump(mode="json") if active else None,
    }


@app.post("/v1/settings/context-packs")
def create_context_pack(
    request: ContextPackCreateRequest,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(require_admin),
) -> ContextPack:
    if request.tenant != user.tenant:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    if not request.pack_id.strip():
        raise HTTPException(status_code=400, detail="pack_id is required")
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    return store.create_context_pack(
        tenant=request.tenant,
        environment=request.environment,
        pack_id=request.pack_id.strip(),
        name=request.name.strip(),
        description=request.description,
        stage_bindings=request.stage_bindings,
        team_bindings=request.team_bindings,
        service_tags=request.service_tags,
        infra_components=request.infra_components,
        dependencies=request.dependencies,
        validity_start=request.validity_start,
        validity_end=request.validity_end,
        updated_by=user.user_id,
    )


@app.post("/v1/settings/context-packs/{pack_id}/artifacts")
def upload_context_pack_artifact(
    pack_id: str,
    request: ContextArtifactUploadRequest,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(require_admin),
) -> ContextPack:
    if request.tenant != user.tenant:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    if not pack_id.strip():
        raise HTTPException(status_code=400, detail="pack_id is required")
    allowed_types = {
        "markdown",
        "md",
        "text",
        "txt",
        "json",
        "yaml",
        "yml",
        "architecture_diagram",
        "operator_notes",
    }
    artifact_type = request.artifact_type.strip().lower()
    if artifact_type not in allowed_types:
        raise HTTPException(status_code=400, detail="unsupported artifact_type")
    try:
        return store.add_context_artifact(
            tenant=request.tenant,
            environment=request.environment,
            pack_id=pack_id,
            filename=request.filename.strip() or f"{artifact_type}-{uuid4()}",
            artifact_type=artifact_type,
            media_type=request.media_type,
            content=request.content,
            operator_notes=request.operator_notes,
            metadata=request.metadata,
            updated_by=user.user_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/settings/context-packs/{pack_id}/activate")
def activate_context_pack(
    pack_id: str,
    request: ContextPackActivateRequest,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(require_admin),
) -> ContextPack:
    if request.tenant != user.tenant:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    try:
        return store.activate_context_pack(
            tenant=request.tenant,
            environment=request.environment,
            pack_id=pack_id,
            version=request.version,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/v1/ui/workflow-layouts/{workflow_key}")
def get_workflow_layout(
    workflow_key: str,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> WorkflowLayoutState | dict[str, str]:
    layout = store.get_workflow_layout(user.tenant, user.user_id, workflow_key)
    if not layout:
        return {"status": "not_found"}
    return layout


@app.put("/v1/ui/workflow-layouts/{workflow_key}")
def upsert_workflow_layout(
    workflow_key: str,
    request: WorkflowLayoutUpsertRequest,
    _: None = Depends(require_api_key),
    user: UserContext = Depends(get_user_context),
) -> WorkflowLayoutState:
    layout = WorkflowLayoutState(
        workflow_key=workflow_key,
        tenant=user.tenant,
        user_id=user.user_id,
        nodes=request.nodes,
        viewport=request.viewport,
        updated_at=datetime.now(timezone.utc),
    )
    return store.upsert_workflow_layout(layout)
