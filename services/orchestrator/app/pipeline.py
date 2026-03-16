from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
import json
from typing import Any

from platform_core.agent_runtime import _execute_mcp_tool, run_planner_agent, run_resolver_agent
from platform_core.evidence_store import EvidenceStore
from platform_core.llm_router import ModelRoute, resolve_model_alias, summarize_with_model_route
from platform_core.mcp_execution import (
    blocked_tool_entries,
    default_prometheus_query,
    extract_artifact_update,
    invocable_tool_names,
    merge_artifact_state,
    resolve_service_aliases,
    seed_artifact_state,
)
from platform_core.mcp_planning import (
    build_mcp_only_plan,
    derive_argument_context,
    filter_tools_by_allowlist,
    select_mcp_tools,
)
from platform_core.models import (
    AgentToolTrace,
    AgentPromptProfile,
    AgentRolloutMode,
    AlertEnvelope,
    AliasDecisionTrace,
    ArtifactState,
    CommanderArbitrationSummary,
    ContextPack,
    ContextReference,
    EvidenceRequirement,
    EvidenceItem,
    Hypothesis,
    InvestigationTeamProfile,
    InvestigationPlan,
    LlmProviderRoute,
    MissionChecklistResult,
    McpServerConfig,
    McpToolDescriptor,
    RcaReport,
    RerunDirective,
    StageEvalRecord,
    ServiceIdentity,
    StageMissionProfile,
    TeamMissionProfile,
    TeamExecutionSummary,
    TeamRcaDraft,
    WorkflowStageId,
)
from platform_core.policy import enforce_budget_policy, enforce_citation_policy
from platform_core.policy_service import PolicyService
from platform_core.publisher import Publisher
from platform_core.resolver import resolve_service_identity
from platform_core.store import store


def _execution_policy(run_context: dict[str, Any] | None) -> str:
    if run_context and isinstance(run_context.get("execution_policy"), str):
        return str(run_context.get("execution_policy") or "mcp_only").strip().lower()
    return "mcp_only"


def _deterministic_service_identity(alert_payload: dict[str, Any]) -> dict[str, Any]:
    alert = AlertEnvelope.model_validate(alert_payload)
    artifact_state = seed_artifact_state(alert_payload, {})
    entities = list(dict.fromkeys(alert.entity_ids))
    candidates = entities if entities else ["unknown-service"]
    artifact_state.service_candidates = candidates[:]
    artifact_state, aliases = resolve_service_aliases(artifact_state)

    identity = resolve_service_identity(
        alert,
        nr_candidates=[artifact_state.resolved_service] if artifact_state.resolved_service else candidates,
        azure_candidates=[],
        cmdb_candidates=[],
        rag_candidates=[],
    )
    payload = identity.model_dump(mode="json")
    payload["stage_reasoning_summary"] = "Deterministic resolver selected service identity from alert entities and artifact heuristics."
    payload["tool_traces"] = []
    payload["skipped_tools"] = []
    payload["artifact_state"] = artifact_state.model_dump(mode="json")
    payload["resolved_aliases"] = [item.model_dump(mode="json") for item in aliases]
    payload["blocked_tools"] = []
    payload["invocable_tools"] = []
    return payload


def _deterministic_mcp_plan(
    investigation_id: str,
    alert_payload: dict[str, Any],
    mcp_tools: list[McpToolDescriptor],
    allowlist: list[str] | None,
    service_identity: dict[str, Any] | None = None,
    artifact_state_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact_state = (
        ArtifactState.model_validate(artifact_state_payload)
        if isinstance(artifact_state_payload, dict)
        else seed_artifact_state(alert_payload, service_identity or {})
    )
    artifact_state, aliases = resolve_service_aliases(artifact_state)
    context = derive_argument_context(alert_payload, service_identity or {}, artifact_state)
    plan, skipped_tools = build_mcp_only_plan(
        investigation_id=investigation_id,
        tools=mcp_tools,
        context=context,
        allowlist=allowlist,
        max_steps=6,
        max_api_calls=10,
        max_stage_wall_clock_seconds=600,
        artifact_state=artifact_state,
        alert_payload=alert_payload,
    )
    enforce_budget_policy(plan)
    payload = plan.model_dump(mode="json")
    payload["stage_reasoning_summary"] = "Deterministic MCP planner applied artifact prerequisites, alias resolution, and budget limits."
    payload["tool_traces"] = []
    payload["skipped_tools"] = skipped_tools
    payload["artifact_state"] = artifact_state.model_dump(mode="json")
    payload["resolved_aliases"] = [item.model_dump(mode="json") for item in aliases]
    payload["blocked_tools"] = blocked_tool_entries(mcp_tools, artifact_state)
    payload["invocable_tools"] = invocable_tool_names(mcp_tools, artifact_state)
    return payload


def _tenant_and_environment(run_context: dict[str, Any] | None) -> tuple[str, str]:
    if not run_context:
        return "default", "prod"
    tenant = str(run_context.get("tenant") or "default")
    environment = str(run_context.get("environment") or "prod")
    return tenant, environment


def _llm_route(run_context: dict[str, Any] | None, tenant: str, environment: str) -> LlmProviderRoute:
    if run_context and isinstance(run_context.get("llm_route"), dict):
        route_payload = run_context["llm_route"]
        return LlmProviderRoute.model_validate(route_payload)
    return store.get_llm_route(tenant, environment)


def _prompt_profile(
    run_context: dict[str, Any] | None,
    tenant: str,
    environment: str,
    stage_id: WorkflowStageId,
) -> AgentPromptProfile:
    if run_context and isinstance(run_context.get("agent_prompt_profiles"), dict):
        profiles_payload = run_context["agent_prompt_profiles"]
        if isinstance(profiles_payload.get(stage_id.value), dict):
            return AgentPromptProfile.model_validate(profiles_payload[stage_id.value])

    existing = store.get_agent_prompt_profile(tenant, environment, stage_id)
    if existing:
        return existing
    return AgentPromptProfile(
        tenant=tenant,
        environment=environment,
        stage_id=stage_id,
        system_prompt="You are an RCA investigation agent.",
        objective_template="Resolve alert {{incident_key}} with evidence-linked reasoning.",
        max_turns=4,
        max_tool_calls=6,
        tool_allowlist=[],
        updated_at=datetime.now(timezone.utc),
        updated_by="system",
    )


def _rollout_mode(run_context: dict[str, Any] | None, tenant: str, environment: str) -> AgentRolloutMode:
    if run_context and run_context.get("agent_rollout_mode"):
        return AgentRolloutMode(str(run_context["agent_rollout_mode"]))
    return store.get_agent_rollout(tenant, environment).mode


def _mcp_servers(run_context: dict[str, Any] | None, tenant: str, environment: str) -> list[McpServerConfig]:
    if run_context and isinstance(run_context.get("mcp_servers"), list):
        return [McpServerConfig.model_validate(item) for item in run_context["mcp_servers"]]
    return store.list_mcp_servers(tenant=tenant, environment=environment)


def _mcp_tools(run_context: dict[str, Any] | None, tenant: str, environment: str) -> list[McpToolDescriptor]:
    if run_context and isinstance(run_context.get("mcp_tools"), list):
        return [McpToolDescriptor.model_validate(item) for item in run_context["mcp_tools"]]
    return store.list_all_mcp_tools(tenant=tenant, environment=environment)


def _agent_tool_precheck(
    *,
    stage_name: str,
    tools: list[McpToolDescriptor],
    allowlist: list[str] | None,
) -> str | None:
    allowlisted = [tool for tool in filter_tools_by_allowlist(tools, allowlist) if tool.read_only]
    if not allowlisted:
        return f"{stage_name} requires at least one enabled read-only MCP tool in the allowlist."
    return None


def _team_profiles(
    run_context: dict[str, Any] | None,
    tenant: str,
    environment: str,
) -> list[InvestigationTeamProfile]:
    if run_context and isinstance(run_context.get("investigation_teams"), list):
        return [InvestigationTeamProfile.model_validate(item) for item in run_context["investigation_teams"]]

    profiles = store.list_investigation_teams(tenant=tenant, environment=environment)
    return [profile for profile in profiles if profile.enabled]


def _stage_mission(
    run_context: dict[str, Any] | None,
    tenant: str,
    environment: str,
    stage_id: WorkflowStageId,
) -> StageMissionProfile:
    if run_context and isinstance(run_context.get("stage_missions"), dict):
        payload = run_context["stage_missions"].get(stage_id.value)
        if isinstance(payload, dict):
            return StageMissionProfile.model_validate(payload)
    mission = store.get_stage_mission(tenant, environment, stage_id)
    if mission:
        return mission
    return StageMissionProfile(
        tenant=tenant,
        environment=environment,
        stage_id=stage_id,
        mission_objective=f"Execute stage {stage_id.value}.",
        required_checks=[],
        allowed_tools=[],
        completion_criteria=[],
        unknown_not_available_rules=[],
        relevance_weights={},
        updated_at=datetime.now(timezone.utc),
        updated_by="system",
    )


def _team_mission(
    run_context: dict[str, Any] | None,
    tenant: str,
    environment: str,
    team_id: str,
) -> TeamMissionProfile:
    if run_context and isinstance(run_context.get("team_missions"), dict):
        payload = run_context["team_missions"].get(team_id)
        if isinstance(payload, dict):
            return TeamMissionProfile.model_validate(payload)
    mission = store.get_team_mission(tenant, environment, team_id)
    if mission:
        return mission
    return TeamMissionProfile(
        team_id=team_id,
        tenant=tenant,
        environment=environment,
        mission_objective=f"Investigate team domain for {team_id}.",
        required_checks=[],
        allowed_tools=[],
        completion_criteria=[],
        unknown_not_available_rules=[],
        relevance_weights={},
        updated_at=datetime.now(timezone.utc),
        updated_by="system",
    )


def _active_context_pack(run_context: dict[str, Any] | None) -> ContextPack | None:
    if run_context and isinstance(run_context.get("active_context_pack"), dict):
        payload = run_context.get("active_context_pack")
        if isinstance(payload, dict) and payload:
            return ContextPack.model_validate(payload)
    return None


def _service_identity_payload(run_context: dict[str, Any] | None) -> dict[str, Any] | None:
    if run_context and isinstance(run_context.get("service_identity"), dict):
        return run_context.get("service_identity")
    return None


def _artifact_state_payload(run_context: dict[str, Any] | None) -> dict[str, Any] | None:
    if not run_context:
        return None
    for key in ("planner_artifact_state", "resolver_artifact_state", "artifact_state"):
        payload = run_context.get(key)
        if isinstance(payload, dict) and payload:
            return payload
    return None


def _stage_rerun_directive(
    run_context: dict[str, Any] | None,
    stage_id: WorkflowStageId,
) -> dict[str, Any] | None:
    if not run_context:
        return None
    payload = run_context.get("active_rerun_directive")
    if not isinstance(payload, dict):
        return None
    if str(payload.get("target_stage") or "") != stage_id.value:
        return None
    return payload


def _context_refs_for_scope(
    *,
    run_context: dict[str, Any] | None,
    tenant: str,
    environment: str,
    stage_id: WorkflowStageId,
    alert_payload: dict[str, Any],
    team_id: str | None = None,
    limit: int = 8,
) -> list[ContextReference]:
    if _active_context_pack(run_context):
        pack = _active_context_pack(run_context)
        if not pack:
            return []
        if pack.stage_bindings and stage_id not in pack.stage_bindings:
            return []
        if pack.team_bindings and team_id and team_id not in pack.team_bindings:
            return []
        refs: list[ContextReference] = []
        alert_terms = set()
        for value in alert_payload.get("entity_ids", []):
            if isinstance(value, str) and value.strip():
                alert_terms.add(value.strip().lower())
        incident = alert_payload.get("incident_key")
        if isinstance(incident, str) and incident.strip():
            alert_terms.add(incident.strip().lower())
        for artifact in pack.artifacts:
            for chunk in artifact.parsed_chunks:
                text = chunk.text.lower()
                score = 0.05
                for term in alert_terms:
                    if term in text:
                        score += 0.35
                if team_id and team_id.lower() in text:
                    score += 0.2
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
                        score=min(score, 1.0),
                    )
                )
        refs.sort(key=lambda item: item.score, reverse=True)
        return refs[: max(1, limit)]

    return store.retrieve_context_refs(
        tenant=tenant,
        environment=environment,
        stage_id=stage_id,
        team_id=team_id,
        alert_payload=alert_payload,
        limit=limit,
    )


def _wildcard_match(value: str, pattern: str) -> bool:
    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    return value == pattern


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        token = value.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _evaluate_mission_checklist(
    *,
    mission_id: str,
    required_checks: list[str],
    observed_checks: set[str],
    unavailable_candidates: set[str] | None = None,
) -> MissionChecklistResult:
    completed: list[str] = []
    failed: list[str] = []
    unavailable: list[str] = []
    unavailable_candidates = unavailable_candidates or set()
    for check in required_checks:
        token = check.strip()
        if not token:
            continue
        if token in unavailable_candidates:
            unavailable.append(token)
            continue
        matched = False
        for observed in observed_checks:
            if observed == token or observed.startswith(f"{token}:"):
                matched = True
                break
            if "*" in token and _wildcard_match(observed, token):
                matched = True
                break
        if matched:
            completed.append(token)
        else:
            failed.append(token)
    return MissionChecklistResult(
        mission_id=mission_id,
        completed=completed,
        failed=failed,
        unavailable=unavailable,
        passed=not failed and not unavailable,
    )


def _mission_metadata(
    *,
    mission_id: str,
    mission_checklist: MissionChecklistResult,
    context_refs: list[ContextReference],
    relevance_weights: dict[str, float],
    unknown_not_available_reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "mission_id": mission_id,
        "mission_checklist": mission_checklist.model_dump(mode="json"),
        "context_refs": [item.model_dump(mode="json") for item in context_refs],
        "relevance_weights": relevance_weights,
        "unknown_not_available_reasons": unknown_not_available_reasons or [],
    }


def _effective_tool_catalog_summary(
    tools: list[McpToolDescriptor],
    allowlist: list[str] | None,
) -> dict[str, Any]:
    allowlisted = [tool for tool in filter_tools_by_allowlist(tools, allowlist) if tool.read_only]
    return {
        "allowlisted_count": len(allowlisted),
        "tools": [
            {
                "name": f"mcp.{tool.server_id}.{tool.tool_name}",
                "phase": tool.phase.value if hasattr(tool.phase, "value") else str(tool.phase),
                "scope_kind": tool.scope_kind.value if hasattr(tool.scope_kind, "value") else str(tool.scope_kind),
                "requires_artifacts": list(tool.requires_artifacts),
            }
            for tool in allowlisted[:40]
        ],
    }


def _alias_decision_trace_from_state(payload: dict[str, Any] | None) -> AliasDecisionTrace | None:
    if not isinstance(payload, dict):
        return None
    state_payload = payload.get("artifact_state")
    if not isinstance(state_payload, dict):
        return None
    trace_payload = state_payload.get("alias_decision_trace")
    if not isinstance(trace_payload, dict):
        return None
    return AliasDecisionTrace.model_validate(trace_payload)


def _resolved_alias_confidence(payload: dict[str, Any] | None) -> float:
    trace = _alias_decision_trace_from_state(payload)
    if trace:
        return float(trace.confidence)
    aliases = payload.get("resolved_aliases") if isinstance(payload, dict) else None
    if isinstance(aliases, list) and aliases:
        last = aliases[-1]
        if isinstance(last, dict):
            return float(last.get("confidence") or 0.0)
    return 0.0


def _service_scope_errors(
    plan_payload: dict[str, Any],
    *,
    alias_min_confidence: float,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(plan_payload, dict):
        return ["invalid_plan_payload"]

    artifact_state = plan_payload.get("artifact_state")
    resolved_service = ""
    if isinstance(artifact_state, dict):
        resolved_service = str(artifact_state.get("resolved_service") or "").strip()
    alias_confidence = _resolved_alias_confidence(plan_payload)
    if not resolved_service:
        errors.append("resolved_service_missing")
    if resolved_service and alias_confidence < alias_min_confidence:
        errors.append("resolved_service_low_confidence")

    ordered_steps = plan_payload.get("ordered_steps")
    if not isinstance(ordered_steps, list):
        return [*errors, "ordered_steps_missing"]

    service_scoped_steps = 0
    for step in ordered_steps:
        if not isinstance(step, dict):
            continue
        args = step.get("mcp_arguments")
        if not isinstance(args, dict):
            continue
        scoped_values = [
            str(args.get(key)).strip()
            for key in ("service", "service_name", "serviceName", "canonical_service_id")
            if args.get(key)
        ]
        if not scoped_values:
            continue
        service_scoped_steps += 1
        if not resolved_service:
            errors.append("service_scoped_step_without_resolved_service")
            continue
        if any(value != resolved_service for value in scoped_values):
            errors.append("service_scoped_step_mismatch")
    if resolved_service and service_scoped_steps == 0:
        errors.append("target_service_not_used_in_plan")
    return sorted(set(errors))


def _parse_rerun_directives(
    text: str | None,
    *,
    allowed_targets: set[WorkflowStageId],
) -> list[RerunDirective]:
    if not text:
        return []
    directives: list[RerunDirective] = []
    current: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("RERUN_STAGE:"):
            if current:
                target_text = str(current.get("target_stage") or "").strip()
                try:
                    target_stage = WorkflowStageId(target_text)
                except Exception:
                    current = {}
                else:
                    if target_stage in allowed_targets:
                        directives.append(
                            RerunDirective(
                                target_stage=target_stage,
                                reason=str(current.get("reason") or "additional_investigation_needed"),
                                additional_objective=str(current.get("additional_objective") or "Collect missing evidence."),
                                expected_evidence=str(current.get("expected_evidence") or "missing evidence"),
                                tool_focus=list(current.get("tool_focus") or []),
                            )
                        )
                    current = {}
            current["target_stage"] = line.split(":", 1)[1].strip()
        elif line.startswith("RERUN_REASON:"):
            current["reason"] = line.split(":", 1)[1].strip()
        elif line.startswith("RERUN_OBJECTIVE:"):
            current["additional_objective"] = line.split(":", 1)[1].strip()
        elif line.startswith("RERUN_EVIDENCE:"):
            current["expected_evidence"] = line.split(":", 1)[1].strip()
        elif line.startswith("RERUN_TOOL_FOCUS:"):
            current["tool_focus"] = [item.strip() for item in line.split(":", 1)[1].split(",") if item.strip()]
    if current:
        target_text = str(current.get("target_stage") or "").strip()
        try:
            target_stage = WorkflowStageId(target_text)
        except Exception:
            return directives
        if target_stage in allowed_targets:
            directives.append(
                RerunDirective(
                    target_stage=target_stage,
                    reason=str(current.get("reason") or "additional_investigation_needed"),
                    additional_objective=str(current.get("additional_objective") or "Collect missing evidence."),
                    expected_evidence=str(current.get("expected_evidence") or "missing evidence"),
                    tool_focus=list(current.get("tool_focus") or []),
                )
            )
    return directives[:2]


def _stage_eval_record(
    *,
    stage_id: WorkflowStageId,
    record_id: str,
    status: str,
    summary: str,
    score: float | None,
    findings: list[str],
    details: dict[str, Any],
) -> dict[str, Any]:
    return StageEvalRecord(
        stage_id=stage_id,
        record_id=record_id,
        status=status,
        summary=summary,
        score=score,
        findings=findings,
        details=details,
    ).model_dump(mode="json")

def _service_identity_diff(deterministic: dict[str, Any], agentic: dict[str, Any]) -> dict[str, Any]:
    deterministic_ambiguous = deterministic.get("ambiguous_candidates") or []
    agent_ambiguous = agentic.get("ambiguous_candidates") or []
    return {
        "canonical_service_id_changed": deterministic.get("canonical_service_id") != agentic.get("canonical_service_id"),
        "deterministic_canonical_service_id": deterministic.get("canonical_service_id"),
        "agent_canonical_service_id": agentic.get("canonical_service_id"),
        "ambiguity_changed": deterministic_ambiguous != agent_ambiguous,
        "deterministic_ambiguous_candidates": deterministic_ambiguous[:3],
        "agent_ambiguous_candidates": agent_ambiguous[:3],
    }


def _plan_diff(deterministic: dict[str, Any], agentic: dict[str, Any]) -> dict[str, Any]:
    def _steps(payload: dict[str, Any]) -> list[tuple[str, str, str | None]]:
        ordered_steps = payload.get("ordered_steps", [])
        if not isinstance(ordered_steps, list):
            return []
        values: list[tuple[str, str, str | None]] = []
        for step in ordered_steps:
            if not isinstance(step, dict):
                continue
            values.append(
                (
                    str(step.get("provider")),
                    str(step.get("capability")),
                    str(step.get("mcp_tool_name")) if step.get("mcp_tool_name") else None,
                )
            )
        return values

    deterministic_steps = _steps(deterministic)
    agent_steps = _steps(agentic)
    return {
        "steps_changed": deterministic_steps != agent_steps,
        "deterministic_steps": deterministic_steps,
        "agent_steps": agent_steps,
        "max_api_calls_changed": deterministic.get("max_api_calls") != agentic.get("max_api_calls"),
        "deterministic_max_api_calls": deterministic.get("max_api_calls"),
        "agent_max_api_calls": agentic.get("max_api_calls"),
    }


def _agent_route_from_llm(route: LlmProviderRoute) -> ModelRoute:
    return ModelRoute(primary=route.primary_model, fallback=route.fallback_model, key_ref=route.key_ref)


def _model_diagnostics(route: LlmProviderRoute) -> tuple[dict[str, str], dict[str, str], str | None]:
    requested = {"primary": route.primary_model, "fallback": route.fallback_model}
    resolved: dict[str, str] = {}
    try:
        resolved["primary"] = resolve_model_alias(route.primary_model)
        resolved["fallback"] = resolve_model_alias(route.fallback_model)
        return requested, resolved, None
    except Exception as exc:
        return requested, resolved, str(exc)


def resolve_service_stage(alert_payload: dict[str, Any], run_context: dict[str, Any] | None = None) -> dict[str, Any]:
    deterministic = _deterministic_service_identity(alert_payload)
    tenant, environment = _tenant_and_environment(run_context)
    rollout = _rollout_mode(run_context, tenant, environment)
    llm_route = _llm_route(run_context, tenant, environment)
    prompt_profile = _prompt_profile(run_context, tenant, environment, WorkflowStageId.RESOLVE_SERVICE_IDENTITY)
    stage_mission = _stage_mission(run_context, tenant, environment, WorkflowStageId.RESOLVE_SERVICE_IDENTITY)
    mcp_servers = _mcp_servers(run_context, tenant, environment)
    mcp_tools = _mcp_tools(run_context, tenant, environment)
    context_refs = _context_refs_for_scope(
        run_context=run_context,
        tenant=tenant,
        environment=environment,
        stage_id=WorkflowStageId.RESOLVE_SERVICE_IDENTITY,
        alert_payload=alert_payload,
        limit=6,
    )
    mission_id = f"stage:{WorkflowStageId.RESOLVE_SERVICE_IDENTITY.value}:{stage_mission.updated_at.isoformat()}"
    requested_model, resolved_model, model_error = _model_diagnostics(llm_route)
    effective_allowlist = prompt_profile.tool_allowlist or stage_mission.allowed_tools
    effective_prompt_profile = (
        prompt_profile.model_copy(update={"tool_allowlist": effective_allowlist})
        if effective_allowlist != prompt_profile.tool_allowlist
        else prompt_profile
    )
    rerun_directive = _stage_rerun_directive(run_context, WorkflowStageId.RESOLVE_SERVICE_IDENTITY)
    if rerun_directive:
        effective_prompt_profile = effective_prompt_profile.model_copy(
            update={
                "objective_template": (
                    f"{effective_prompt_profile.objective_template} "
                    f"Additional rerun objective: {rerun_directive.get('additional_objective') or rerun_directive.get('reason')}"
                )
            }
        )
    tooling_error = _agent_tool_precheck(
        stage_name="resolve_service_identity",
        tools=mcp_tools,
        allowlist=effective_allowlist,
    )
    tool_catalog_summary = _effective_tool_catalog_summary(mcp_tools, effective_allowlist)

    def _attach_mission_fields(payload: dict[str, Any]) -> dict[str, Any]:
        observed_checks: set[str] = set()
        entity_ids = alert_payload.get("entity_ids")
        alias_trace = _alias_decision_trace_from_state(payload)
        selected_service = ""
        if alias_trace and alias_trace.selected_candidate:
            selected_service = str(alias_trace.selected_candidate).strip()
        elif isinstance(payload.get("canonical_service_id"), str):
            candidate = str(payload.get("canonical_service_id") or "").strip()
            if candidate and candidate.lower() not in {"unknown", "unresolved"}:
                selected_service = candidate
        if isinstance(entity_ids, list) and entity_ids:
            observed_checks.add("alert_entities_reviewed")
        if selected_service:
            observed_checks.add("canonical_service_selected")
        if selected_service and payload.get("confidence") is not None:
            observed_checks.add("confidence_reported")
        ambiguous = payload.get("ambiguous_candidates")
        if isinstance(ambiguous, list) and ambiguous:
            observed_checks.add("ambiguity_listed_when_present")
        if context_refs:
            observed_checks.add("context_available")

        unavailable_candidates: set[str] = set()
        if not (isinstance(entity_ids, list) and entity_ids):
            unavailable_candidates.add("missing_entity_context")
        checklist = _evaluate_mission_checklist(
            mission_id=mission_id,
            required_checks=stage_mission.required_checks,
            observed_checks=observed_checks,
            unavailable_candidates=unavailable_candidates,
        )
        unknown_reasons = [*checklist.unavailable]
        payload.update(
            _mission_metadata(
                mission_id=mission_id,
                mission_checklist=checklist,
                context_refs=context_refs,
                relevance_weights=stage_mission.relevance_weights,
                unknown_not_available_reasons=unknown_reasons,
            )
        )
        existing = str(payload.get("stage_reasoning_summary") or "").strip()
        mission_suffix = (
            f" Mission objective: {stage_mission.mission_objective}. "
            f"Context refs used: {len(context_refs)}."
        )
        payload["stage_reasoning_summary"] = f"{existing}{mission_suffix}".strip()
        return payload

    def _finalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        payload = _attach_mission_fields(payload)
        alias_trace = _alias_decision_trace_from_state(payload)
        findings: list[str] = []
        status = "pass"
        score = _resolved_alias_confidence(payload)
        if not alias_trace or not alias_trace.selected_candidate:
            status = "fail"
            findings.append("resolved_service_missing")
        else:
            if alias_trace.matched_term_source == "summary":
                status = "fail"
                findings.append("summary_only_alias_resolution")
            elif alias_trace.confidence < stage_mission.alias_min_confidence:
                status = "warn"
                findings.append("alias_confidence_below_threshold")
            if alias_trace.ambiguous_candidates:
                if status == "pass":
                    status = "warn"
                findings.append("alias_resolution_ambiguous")
        payload["alias_decision_trace"] = alias_trace.model_dump(mode="json") if alias_trace else None
        payload["effective_prompt_snapshot"] = effective_prompt_profile.model_dump(mode="json")
        payload["effective_mission_snapshot"] = stage_mission.model_dump(mode="json")
        payload["effective_tool_catalog_summary"] = tool_catalog_summary
        payload["rerun_directives"] = []
        payload["stage_eval_records"] = [
            _stage_eval_record(
                stage_id=WorkflowStageId.RESOLVE_SERVICE_IDENTITY,
                record_id="resolver_alias_quality",
                status=status,
                summary=(
                    f"Resolver selected {payload.get('canonical_service_id') or 'unknown'} "
                    f"from {alias_trace.matched_term_source if alias_trace else 'no source'}."
                ),
                score=score if alias_trace else 0.0,
                findings=findings,
                details={
                    "resolved_service": payload.get("canonical_service_id"),
                    "alias_trace": payload.get("alias_decision_trace"),
                    "threshold": stage_mission.alias_min_confidence,
                },
            )
        ]
        return payload

    if _execution_policy(run_context) != "mcp_only":
        raise ValueError("Unsupported execution policy. Expected mcp_only.")

    if rollout == AgentRolloutMode.ACTIVE:
        if model_error:
            raise RuntimeError(f"Resolver model failure in active mode: {model_error}")
        if tooling_error:
            raise RuntimeError(tooling_error)
        agent_result = run_resolver_agent(
            alert_payload=alert_payload,
            model_route=_agent_route_from_llm(llm_route),
            prompt_profile=effective_prompt_profile,
            mcp_servers=mcp_servers,
            mcp_tools=mcp_tools,
        )
        if agent_result.model_error:
            raise RuntimeError(f"Resolver model failure in active mode: {agent_result.model_error}")
        validated = ServiceIdentity.model_validate(agent_result.payload)
        payload = validated.model_dump(mode="json")
        payload["llm_model_used"] = agent_result.llm_model_used
        payload["llm_summary"] = agent_result.llm_summary
        payload["stage_reasoning_summary"] = agent_result.stage_reasoning_summary
        payload["tool_traces"] = [trace.model_dump(mode="json") for trace in agent_result.tool_traces]
        payload["skipped_tools"] = agent_result.skipped_tools
        payload["artifact_state"] = agent_result.artifact_state or {}
        payload["resolved_aliases"] = agent_result.resolved_aliases or []
        payload["blocked_tools"] = agent_result.blocked_tools or []
        payload["invocable_tools"] = agent_result.invocable_tools or []
        payload["requested_model"] = agent_result.requested_model
        payload["resolved_model"] = {**agent_result.resolved_model, "used": agent_result.llm_model_used}
        payload["model_error"] = agent_result.model_error
        payload["agent_rollout_mode"] = rollout.value
        payload["execution_policy"] = "mcp_only"
        return _finalize_payload(payload)

    if model_error or tooling_error:
        error_text = model_error or tooling_error or "Unknown compare-mode agent error."
        deterministic["agent_compare"] = {"agent_error": error_text}
        deterministic["agent_rollout_mode"] = rollout.value
        deterministic["requested_model"] = requested_model
        deterministic["resolved_model"] = resolved_model
        deterministic["model_error"] = error_text
        deterministic["stage_reasoning_summary"] = (
            "Compare mode: deterministic output active. "
            f"Agent candidate failed preflight and was recorded for scoring ({error_text})."
        )
        deterministic["skipped_tools"] = []
        deterministic["execution_policy"] = "mcp_only"
        return _finalize_payload(deterministic)

    try:
        agent_result = run_resolver_agent(
            alert_payload=alert_payload,
            model_route=_agent_route_from_llm(llm_route),
            prompt_profile=effective_prompt_profile,
            mcp_servers=mcp_servers,
            mcp_tools=mcp_tools,
        )
        validated = ServiceIdentity.model_validate(agent_result.payload)
        compare_diff = _service_identity_diff(deterministic, validated.model_dump(mode="json"))
        deterministic["agent_compare"] = compare_diff
        deterministic["agent_rollout_mode"] = rollout.value
        deterministic["llm_model_used"] = agent_result.llm_model_used
        deterministic["llm_summary"] = agent_result.llm_summary
        deterministic["stage_reasoning_summary"] = (
            "Compare mode: deterministic output active. "
            f"Agent candidate from {agent_result.llm_model_used} captured for scoring."
        )
        deterministic["tool_traces"] = [trace.model_dump(mode="json") for trace in agent_result.tool_traces]
        deterministic["skipped_tools"] = agent_result.skipped_tools
        deterministic["artifact_state"] = agent_result.artifact_state or {}
        deterministic["resolved_aliases"] = agent_result.resolved_aliases or []
        deterministic["blocked_tools"] = agent_result.blocked_tools or []
        deterministic["invocable_tools"] = agent_result.invocable_tools or []
        deterministic["requested_model"] = agent_result.requested_model
        deterministic["resolved_model"] = (
            {**agent_result.resolved_model, "used": agent_result.llm_model_used}
            if agent_result.resolved_model
            else {}
        )
        deterministic["model_error"] = agent_result.model_error
        if agent_result.model_error:
            deterministic["agent_compare"] = {"agent_error": agent_result.model_error}
            deterministic["stage_reasoning_summary"] = (
                "Compare mode: deterministic output active. "
                f"Agent model call failed and was recorded ({agent_result.model_error})."
            )
    except Exception as exc:
        deterministic["agent_compare"] = {"agent_error": str(exc)}
        deterministic["agent_rollout_mode"] = rollout.value
        deterministic["requested_model"] = requested_model
        deterministic["resolved_model"] = resolved_model
        deterministic["model_error"] = str(exc) or model_error
        deterministic["stage_reasoning_summary"] = (
            "Compare mode: deterministic output active. "
            f"Agent candidate failed and was recorded for scoring ({exc})."
        )
        deterministic["skipped_tools"] = []
    deterministic["execution_policy"] = "mcp_only"
    return _finalize_payload(deterministic)


def build_plan_stage(
    investigation_id: str,
    alert_payload: dict[str, Any],
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tenant, environment = _tenant_and_environment(run_context)
    rollout = _rollout_mode(run_context, tenant, environment)
    llm_route = _llm_route(run_context, tenant, environment)
    prompt_profile = _prompt_profile(run_context, tenant, environment, WorkflowStageId.BUILD_INVESTIGATION_PLAN)
    stage_mission = _stage_mission(run_context, tenant, environment, WorkflowStageId.BUILD_INVESTIGATION_PLAN)
    mcp_servers = _mcp_servers(run_context, tenant, environment)
    mcp_tools = _mcp_tools(run_context, tenant, environment)
    context_refs = _context_refs_for_scope(
        run_context=run_context,
        tenant=tenant,
        environment=environment,
        stage_id=WorkflowStageId.BUILD_INVESTIGATION_PLAN,
        alert_payload=alert_payload,
        limit=8,
    )
    mission_id = f"stage:{WorkflowStageId.BUILD_INVESTIGATION_PLAN.value}:{stage_mission.updated_at.isoformat()}"
    requested_model, resolved_model, model_error = _model_diagnostics(llm_route)
    effective_allowlist = prompt_profile.tool_allowlist or stage_mission.allowed_tools
    effective_prompt_profile = (
        prompt_profile.model_copy(update={"tool_allowlist": effective_allowlist})
        if effective_allowlist != prompt_profile.tool_allowlist
        else prompt_profile
    )
    rerun_directive = _stage_rerun_directive(run_context, WorkflowStageId.BUILD_INVESTIGATION_PLAN)
    if rerun_directive:
        effective_prompt_profile = effective_prompt_profile.model_copy(
            update={
                "objective_template": (
                    f"{effective_prompt_profile.objective_template} "
                    f"Additional rerun objective: {rerun_directive.get('additional_objective') or rerun_directive.get('reason')}"
                )
            }
        )
    tooling_error = _agent_tool_precheck(
        stage_name="build_investigation_plan",
        tools=mcp_tools,
        allowlist=effective_allowlist,
    )
    tool_catalog_summary = _effective_tool_catalog_summary(mcp_tools, effective_allowlist)
    service_identity = _service_identity_payload(run_context) or {}
    artifact_state_payload = _artifact_state_payload(run_context)

    def _attach_mission_fields(payload: dict[str, Any]) -> dict[str, Any]:
        ordered_steps = payload.get("ordered_steps")
        observed_checks: set[str] = set()
        if isinstance(ordered_steps, list) and ordered_steps:
            observed_checks.add("ordered_steps_present")
        if isinstance(payload.get("max_api_calls"), int):
            observed_checks.add("max_api_calls_present")
        if isinstance(ordered_steps, list):
            if all(
                isinstance(item, dict)
                and item.get("provider") == "mcp"
                and item.get("execution_source") == "mcp"
                for item in ordered_steps
            ):
                observed_checks.add("mcp_only_steps")
            target_scoped = False
            for step in ordered_steps:
                if not isinstance(step, dict):
                    continue
                args = step.get("mcp_arguments")
                if isinstance(args, dict) and any(args.get(key) for key in ("service", "service_name", "serviceName")):
                    target_scoped = True
                    break
            if target_scoped:
                observed_checks.add("target_service_scoped")
        if isinstance(payload.get("max_stage_wall_clock_seconds"), int):
            observed_checks.add("budget_limits_applied")
        if context_refs:
            observed_checks.add("context_available")

        unavailable_candidates: set[str] = set()
        if not (isinstance(ordered_steps, list) and ordered_steps):
            unavailable_candidates.add("no_invocable_tools")

        checklist = _evaluate_mission_checklist(
            mission_id=mission_id,
            required_checks=stage_mission.required_checks,
            observed_checks=observed_checks,
            unavailable_candidates=unavailable_candidates,
        )
        unknown_reasons = [*checklist.unavailable]
        payload.update(
            _mission_metadata(
                mission_id=mission_id,
                mission_checklist=checklist,
                context_refs=context_refs,
                relevance_weights=stage_mission.relevance_weights,
                unknown_not_available_reasons=unknown_reasons,
            )
        )
        existing = str(payload.get("stage_reasoning_summary") or "").strip()
        mission_suffix = (
            f" Mission objective: {stage_mission.mission_objective}. "
            f"Context refs used: {len(context_refs)}."
        )
        payload["stage_reasoning_summary"] = f"{existing}{mission_suffix}".strip()
        return payload

    def _finalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        payload = _attach_mission_fields(payload)
        scope_errors = _service_scope_errors(
            payload,
            alias_min_confidence=stage_mission.alias_min_confidence,
        )
        parsed_directives = _parse_rerun_directives(
            payload.get("llm_summary"),
            allowed_targets={
                WorkflowStageId.RESOLVE_SERVICE_IDENTITY,
                WorkflowStageId.BUILD_INVESTIGATION_PLAN,
            },
        )
        if scope_errors and not parsed_directives:
            target = (
                WorkflowStageId.RESOLVE_SERVICE_IDENTITY
                if any(
                    error in scope_errors
                    for error in (
                        "resolved_service_missing",
                        "resolved_service_low_confidence",
                        "service_scoped_step_mismatch",
                    )
                )
                else WorkflowStageId.BUILD_INVESTIGATION_PLAN
            )
            parsed_directives = [
                RerunDirective(
                    target_stage=target,
                    reason="service_scope_invalid",
                    additional_objective=(
                        "Re-run service resolution and planning with stronger service anchoring. "
                        "Do not emit service-scoped MCP steps until the alerted service is resolved confidently."
                    ),
                    expected_evidence="resolved_service aligned to explicit alert service",
                    tool_focus=["mcp.jaeger.get_services", "mcp.prometheus.list_label_values"],
                )
            ]
        alias_trace = _alias_decision_trace_from_state(payload)
        findings = list(scope_errors)
        status = "pass" if not scope_errors else "fail"
        if alias_trace and alias_trace.ambiguous_candidates and status == "pass":
            status = "warn"
            findings.append("alias_resolution_ambiguous")
        payload["plan_valid"] = not scope_errors
        payload["plan_validation_errors"] = scope_errors
        payload["alias_decision_trace"] = alias_trace.model_dump(mode="json") if alias_trace else None
        payload["effective_prompt_snapshot"] = effective_prompt_profile.model_dump(mode="json")
        payload["effective_mission_snapshot"] = stage_mission.model_dump(mode="json")
        payload["effective_tool_catalog_summary"] = tool_catalog_summary
        payload["rerun_directives"] = [item.model_dump(mode="json") for item in parsed_directives]
        payload["stage_eval_records"] = [
            _stage_eval_record(
                stage_id=WorkflowStageId.BUILD_INVESTIGATION_PLAN,
                record_id="planner_service_correctness",
                status=status,
                summary=(
                    "Planner emitted service-correct MCP steps."
                    if not scope_errors
                    else "Planner emitted invalid or weakly-scoped service steps."
                ),
                score=max(0.0, 1.0 - (0.25 * len(scope_errors))),
                findings=findings,
                details={
                    "resolved_service": ((payload.get("artifact_state") or {}).get("resolved_service") if isinstance(payload.get("artifact_state"), dict) else None),
                    "alias_trace": payload.get("alias_decision_trace"),
                    "validation_errors": scope_errors,
                },
            )
        ]
        existing = str(payload.get("stage_reasoning_summary") or "").strip()
        if scope_errors:
            payload["stage_reasoning_summary"] = (
                f"{existing} Planner validation failed: {', '.join(scope_errors)}."
            ).strip()
        return payload

    if _execution_policy(run_context) != "mcp_only":
        raise ValueError("Unsupported execution policy. Expected mcp_only.")

    deterministic = _deterministic_mcp_plan(
        investigation_id,
        alert_payload,
        mcp_tools,
        effective_allowlist,
        service_identity,
        artifact_state_payload,
    )

    if rollout == AgentRolloutMode.ACTIVE:
        if model_error:
            raise RuntimeError(f"Planner model failure in active mode: {model_error}")
        if tooling_error:
            raise RuntimeError(tooling_error)
        agent_result = run_planner_agent(
            investigation_id=investigation_id,
            alert_payload=alert_payload,
            model_route=_agent_route_from_llm(llm_route),
            prompt_profile=effective_prompt_profile,
            mcp_servers=mcp_servers,
            mcp_tools=mcp_tools,
            service_identity=service_identity,
            artifact_state_payload=artifact_state_payload,
        )
        if agent_result.model_error:
            raise RuntimeError(f"Planner model failure in active mode: {agent_result.model_error}")
        validated = InvestigationPlan.model_validate(agent_result.payload)
        payload = validated.model_dump(mode="json")
        payload["llm_model_used"] = agent_result.llm_model_used
        payload["llm_summary"] = agent_result.llm_summary
        payload["stage_reasoning_summary"] = agent_result.stage_reasoning_summary
        payload["tool_traces"] = [trace.model_dump(mode="json") for trace in agent_result.tool_traces]
        payload["skipped_tools"] = agent_result.skipped_tools
        payload["artifact_state"] = agent_result.artifact_state or {}
        payload["resolved_aliases"] = agent_result.resolved_aliases or []
        payload["blocked_tools"] = agent_result.blocked_tools or []
        payload["invocable_tools"] = agent_result.invocable_tools or []
        payload["requested_model"] = agent_result.requested_model
        payload["resolved_model"] = {**agent_result.resolved_model, "used": agent_result.llm_model_used}
        payload["model_error"] = agent_result.model_error
        payload["agent_rollout_mode"] = rollout.value
        payload["execution_policy"] = "mcp_only"
        return _finalize_payload(payload)

    if model_error or tooling_error:
        error_text = model_error or tooling_error or "Unknown compare-mode agent error."
        deterministic["agent_compare"] = {"agent_error": error_text}
        deterministic["agent_rollout_mode"] = rollout.value
        deterministic["requested_model"] = requested_model
        deterministic["resolved_model"] = resolved_model
        deterministic["model_error"] = error_text
        deterministic["stage_reasoning_summary"] = (
            "Compare mode: deterministic plan active. "
            f"Agent candidate failed preflight and was recorded for scoring ({error_text})."
        )
        deterministic["execution_policy"] = "mcp_only"
        return _finalize_payload(deterministic)

    try:
        agent_result = run_planner_agent(
            investigation_id=investigation_id,
            alert_payload=alert_payload,
            model_route=_agent_route_from_llm(llm_route),
            prompt_profile=effective_prompt_profile,
            mcp_servers=mcp_servers,
            mcp_tools=mcp_tools,
            service_identity=service_identity,
            artifact_state_payload=artifact_state_payload,
        )
        validated = InvestigationPlan.model_validate(agent_result.payload)
        compare_diff = _plan_diff(deterministic, validated.model_dump(mode="json"))
        deterministic["agent_compare"] = compare_diff
        deterministic["agent_rollout_mode"] = rollout.value
        deterministic["llm_model_used"] = agent_result.llm_model_used
        deterministic["llm_summary"] = agent_result.llm_summary
        deterministic["stage_reasoning_summary"] = (
            "Compare mode: deterministic plan active. "
            f"Agent candidate from {agent_result.llm_model_used} captured for scoring."
        )
        deterministic["tool_traces"] = [trace.model_dump(mode="json") for trace in agent_result.tool_traces]
        deterministic["skipped_tools"] = agent_result.skipped_tools
        deterministic["artifact_state"] = agent_result.artifact_state or {}
        deterministic["resolved_aliases"] = agent_result.resolved_aliases or []
        deterministic["blocked_tools"] = agent_result.blocked_tools or []
        deterministic["invocable_tools"] = agent_result.invocable_tools or []
        deterministic["requested_model"] = agent_result.requested_model
        deterministic["resolved_model"] = (
            {**agent_result.resolved_model, "used": agent_result.llm_model_used}
            if agent_result.resolved_model
            else {}
        )
        deterministic["model_error"] = agent_result.model_error
        if agent_result.model_error:
            deterministic["agent_compare"] = {"agent_error": agent_result.model_error}
            deterministic["stage_reasoning_summary"] = (
                "Compare mode: deterministic plan active. "
                f"Agent model call failed and was recorded ({agent_result.model_error})."
            )
    except Exception as exc:
        deterministic["agent_compare"] = {"agent_error": str(exc)}
        deterministic["agent_rollout_mode"] = rollout.value
        deterministic["requested_model"] = requested_model
        deterministic["resolved_model"] = resolved_model
        deterministic["model_error"] = str(exc) or model_error
        deterministic["stage_reasoning_summary"] = (
            "Compare mode: deterministic plan active. "
            f"Agent candidate failed and was recorded for scoring ({exc})."
        )
    deterministic["execution_policy"] = "mcp_only"
    return _finalize_payload(deterministic)


def _extract_signals(result: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                signals.append(item)
    if signals:
        return signals
    return [result if isinstance(result, dict) else {"result": result}]


def _parse_confidence(text: str, fallback: float = 0.65) -> float:
    lowered = text.strip().lower()
    if not lowered:
        return fallback
    try:
        value = float(lowered)
        if value > 1:
            value = value / 100.0
        return max(0.0, min(1.0, value))
    except ValueError:
        return fallback


def _build_team_draft(
    *,
    team_id: str,
    status: str,
    objective_prompt: str,
    mission_id: str,
    mission_checklist: MissionChecklistResult,
    context_refs: list[ContextReference],
    relevance_weights: dict[str, float],
    unknown_not_available_reasons: list[str],
    completeness_status: str,
    llm_route: LlmProviderRoute,
    alert: AlertEnvelope,
    evidence_items: list[EvidenceItem],
    citations: list[str],
    tool_traces: list[AgentToolTrace],
    skipped_tools: list[dict[str, Any]],
    artifact_state: ArtifactState | None,
    resolved_aliases: list[dict[str, Any]] | None,
    blocked_tools: list[dict[str, Any]],
    invocable_tools: list[str],
    fallback_summary: str,
) -> TeamRcaDraft:
    if not evidence_items:
        return TeamRcaDraft(
            team_id=team_id,
            status=status,
            summary=fallback_summary,
            mission_id=mission_id,
            mission_checklist=mission_checklist,
            context_refs=context_refs,
            unknown_not_available_reasons=unknown_not_available_reasons or ["No evidence collected by this team."],
            relevance_weights=relevance_weights,
            completeness_status=completeness_status,
            hypotheses=[],
            confidence=0.0,
            supporting_citations=[],
            unknowns=["No evidence collected by this team."],
            tool_traces=tool_traces,
            skipped_tools=skipped_tools,
            artifact_state=artifact_state,
            resolved_aliases=resolved_aliases or [],
            blocked_tools=blocked_tools,
            invocable_tools=invocable_tools,
        )

    evidence_digest = "\n\n".join(
        f"[{item.provider}.{item.evidence_type}] {item.citation_id}\n{_evidence_text(item)}"
        for item in evidence_items[:6]
    )
    context_digest = "\n".join(f"- {item.summary} ({item.context_citation_id})" for item in context_refs[:6])
    prompt = (
        f"{objective_prompt}\n"
        f"Alert key: {alert.incident_key}\n"
        f"Mission ID: {mission_id}\n"
        f"Mission completeness status: {completeness_status}\n"
        f"Mission required checks completed: {', '.join(mission_checklist.completed) or 'none'}\n"
        f"Mission failed checks: {', '.join(mission_checklist.failed) or 'none'}\n"
        f"Mission unavailable checks: {', '.join(mission_checklist.unavailable) or 'none'}\n"
        "You are one incident response team. Draft a concise team assessment from evidence.\n"
        "Output format:\n"
        "H1: ...\n"
        "H2: ...\n"
        "H3: ...\n"
        "CONFIDENCE: <0-1>\n"
        "UNKNOWNS: item1 | item2\n"
        "SUMMARY: one sentence.\n"
        f"Context references:\n{context_digest or '- none'}\n"
        f"Evidence:\n{evidence_digest}"
    )

    hypotheses: list[Hypothesis] = []
    confidence = 0.65
    unknowns: list[str] = []
    summary = fallback_summary

    try:
        model_used, llm_summary = summarize_with_model_route(
            _agent_route_from_llm(llm_route),
            prompt,
            system_prompt="You are an incident response specialist. Be concise and evidence-grounded.",
            max_tokens=450,
        )
        summary = llm_summary.strip() or fallback_summary
        parsed_h: list[str] = []
        for line in llm_summary.splitlines():
            text = line.strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered.startswith(("h1:", "h2:", "h3:")):
                parsed_h.append(text.split(":", 1)[1].strip())
            elif lowered.startswith("confidence:"):
                confidence = _parse_confidence(text.split(":", 1)[1].strip(), confidence)
            elif lowered.startswith("unknowns:"):
                unknowns = [item.strip() for item in text.split(":", 1)[1].split("|") if item.strip()]
            elif lowered.startswith("summary:"):
                summary = text.split(":", 1)[1].strip() or summary

        if not parsed_h:
            parsed_h = [f"{team_id} team found issue signals requiring follow-up investigation."]

        for idx, statement in enumerate(parsed_h[:3]):
            hypotheses.append(
                Hypothesis(
                    statement=statement,
                    confidence=max(0.35, confidence - (idx * 0.12)),
                    supporting_citations=citations[:3],
                    counter_evidence_citations=[],
                )
            )
        if model_used:
            summary = f"{summary} (model: {model_used})"
    except Exception as exc:
        hypotheses.append(
            Hypothesis(
                statement=f"{team_id} team collected evidence but model summarization failed: {exc}",
                confidence=0.4,
                supporting_citations=citations[:3],
                counter_evidence_citations=[],
            )
        )
        unknowns.append("Model summarization failed for team draft.")

    return TeamRcaDraft(
        team_id=team_id,
        status=status,
        summary=summary,
        mission_id=mission_id,
        mission_checklist=mission_checklist,
        context_refs=context_refs,
        unknown_not_available_reasons=unknown_not_available_reasons,
        relevance_weights=relevance_weights,
        completeness_status=completeness_status,
        hypotheses=hypotheses,
        confidence=confidence,
        supporting_citations=citations,
        unknowns=unknowns,
        tool_traces=tool_traces,
        skipped_tools=skipped_tools,
        artifact_state=artifact_state,
        resolved_aliases=resolved_aliases or [],
        blocked_tools=blocked_tools,
        invocable_tools=invocable_tools,
    )


def collect_evidence_stage(
    investigation_id: str,
    alert_payload: dict[str, Any],
    plan_payload: dict[str, Any],
    run_context: dict[str, Any] | None = None,
    early_stop_min_citations: int = 3,
) -> dict[str, Any]:
    plan = InvestigationPlan.model_validate(plan_payload)
    alert = AlertEnvelope.model_validate(alert_payload)
    _ = early_stop_min_citations  # retained for backwards compatibility

    if _execution_policy(run_context) != "mcp_only":
        raise ValueError("Unsupported execution policy. Expected mcp_only.")
    non_mcp_steps = [step for step in plan.ordered_steps if (step.provider != "mcp" or step.execution_source != "mcp")]
    if non_mcp_steps:
        raise ValueError("collect_evidence_stage requires MCP-only plan steps in mcp_only mode.")

    tenant, environment = _tenant_and_environment(run_context)
    llm_route = _llm_route(run_context, tenant, environment)
    mcp_tools = _mcp_tools(run_context, tenant, environment)
    teams = [team for team in _team_profiles(run_context, tenant, environment) if team.enabled]
    stage_mission = _stage_mission(run_context, tenant, environment, WorkflowStageId.COLLECT_EVIDENCE)
    stage_rerun_directive = _stage_rerun_directive(run_context, WorkflowStageId.COLLECT_EVIDENCE)
    stage_context_refs = _context_refs_for_scope(
        run_context=run_context,
        tenant=tenant,
        environment=environment,
        stage_id=WorkflowStageId.COLLECT_EVIDENCE,
        alert_payload=alert_payload,
        limit=10,
    )
    stage_mission_id = f"stage:{WorkflowStageId.COLLECT_EVIDENCE.value}:{stage_mission.updated_at.isoformat()}"

    if run_context and isinstance(run_context.get("mcp_servers"), list):
        servers = {
            server.server_id: server
            for server in [McpServerConfig.model_validate(item) for item in run_context["mcp_servers"]]
            if server.enabled
        }
    else:
        servers = {
            server.server_id: server
            for server in store.list_mcp_servers(tenant=tenant, environment=environment)
            if server.enabled
        }

    policy_service = PolicyService()
    evidence_store = EvidenceStore()
    service_identity_payload = _service_identity_payload(run_context) or {}
    seeded_artifact_state = (
        ArtifactState.model_validate(plan_payload.get("artifact_state"))
        if isinstance(plan_payload.get("artifact_state"), dict)
        else seed_artifact_state(alert_payload, service_identity_payload)
    )
    seeded_artifact_state, _ = resolve_service_aliases(seeded_artifact_state)
    context = derive_argument_context(alert_payload, service_identity_payload, seeded_artifact_state)
    timeline: list[str] = []
    execution_trace: list[dict[str, Any]] = []
    skipped_tools: list[dict[str, Any]] = []
    team_reports: list[TeamRcaDraft] = []
    team_execution: list[TeamExecutionSummary] = []

    def _matches_tool_pattern(tool_name: str, patterns: list[str]) -> bool:
        for pattern in patterns:
            token = pattern.strip()
            if not token:
                continue
            if _wildcard_match(tool_name, token):
                return True
        return False

    def _alert_symptom_tokens() -> set[str]:
        values: list[str] = []
        raw = alert.raw_payload if isinstance(alert.raw_payload, dict) else {}
        for key in ("title", "summary", "customer_impact"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                values.append(value)
        symptoms = raw.get("symptoms")
        if isinstance(symptoms, list):
            values.extend([item for item in symptoms if isinstance(item, str) and item.strip()])
        tokens: set[str] = set()
        for value in values:
            tokens.update(part for part in json.dumps(value).lower().replace('"', " ").replace("/", " ").split() if part)
        return tokens

    symptom_tokens = _alert_symptom_tokens()

    def _required_evidence_requirements(team_mission: TeamMissionProfile) -> list[EvidenceRequirement]:
        if not team_mission.evidence_requirements:
            return []
        required_classes = {
            requirement.evidence_class
            for requirement in team_mission.evidence_requirements
            if not requirement.required_symptoms
        }
        for token, classes in team_mission.symptom_overrides.items():
            if token.lower() in symptom_tokens:
                required_classes.update(classes)
        for requirement in team_mission.evidence_requirements:
            if any(symptom.lower() in symptom_tokens for symptom in requirement.required_symptoms):
                required_classes.add(requirement.evidence_class)
        return [req for req in team_mission.evidence_requirements if req.evidence_class in required_classes]

    def _requirement_satisfied(
        requirement: EvidenceRequirement,
        traces: list[AgentToolTrace],
        resolved_service: str | None,
    ) -> bool:
        resolved_service = (resolved_service or "").strip()
        for trace in traces:
            if not trace.success:
                continue
            if not _matches_tool_pattern(trace.tool_name, requirement.tool_patterns):
                continue
            if requirement.query_scope == "any":
                return True
            args_summary = trace.args_summary or {}
            if requirement.query_scope == "change":
                return True
            query_text = str(args_summary.get("query") or "").lower()
            if requirement.query_scope == "service":
                if resolved_service and resolved_service.lower() in query_text:
                    return True
                if any(str(args_summary.get(key) or "").strip() == resolved_service for key in ("service", "service_name", "serviceName")):
                    return True
                continue
            if requirement.query_scope == "global":
                if query_text and (not resolved_service or resolved_service.lower() not in query_text):
                    return True
                if trace.tool_name.endswith("get_annotations") or trace.tool_name.endswith("get_annotation_tags"):
                    return True
        return False

    def _unmet_evidence_requirements(
        team_mission: TeamMissionProfile,
        traces: list[AgentToolTrace],
        resolved_service: str | None,
    ) -> list[EvidenceRequirement]:
        active = _required_evidence_requirements(team_mission)
        return [req for req in active if not _requirement_satisfied(req, traces, resolved_service)]

    def _priority_requirement_tools(
        team_mission: TeamMissionProfile,
        allowlisted: list[McpToolDescriptor],
        team_context: dict[str, Any],
        team_artifact_state: ArtifactState,
        effective_allowlist: list[str],
        team_traces: list[AgentToolTrace],
        max_parallel_calls: int,
        remaining_budget: int,
    ) -> tuple[list[Any], list[dict[str, Any]], list[str]]:
        unmet = _unmet_evidence_requirements(team_mission, team_traces, team_artifact_state.resolved_service)
        if not unmet:
            return [], [], []
        focus: list[str] = []
        for requirement in unmet:
            matching = [
                tool for tool in allowlisted
                if _matches_tool_pattern(f"mcp.{tool.server_id}.{tool.tool_name}", requirement.tool_patterns)
            ]
            if not matching:
                continue
            selected, req_skipped = select_mcp_tools(
                matching,
                team_context,
                allowlist=effective_allowlist,
                max_tools=max(1, min(max_parallel_calls, remaining_budget)),
                mode="evidence",
                light_probe_only=False,
                artifact_state=team_artifact_state,
                alert_payload=alert_payload,
            )
            if not selected:
                selected, discovery_skipped = select_mcp_tools(
                    matching,
                    team_context,
                    allowlist=effective_allowlist,
                    max_tools=max(1, min(max_parallel_calls, remaining_budget)),
                    mode="discovery",
                    light_probe_only=True,
                    artifact_state=team_artifact_state,
                    alert_payload=alert_payload,
                )
                req_skipped.extend(discovery_skipped)
            for planned in selected:
                if planned.descriptor.server_id.lower() == "prometheus" and planned.descriptor.tool_name in {"query_instant", "query_range"}:
                    query_mode = "throughput"
                    if any(token in symptom_tokens for token in ("error", "5xx", "exception", "fail")):
                        query_mode = "error"
                    elif any(token in symptom_tokens for token in ("latency", "slow", "slowness", "duration", "timeout")):
                        query_mode = "latency"
                    scope = "service" if requirement.query_scope == "service" else "global"
                    planned.arguments["query"] = default_prometheus_query(
                        team_artifact_state.alert_terms,
                        team_artifact_state.resolved_service or "",
                        query_mode,
                        scope=scope,
                    )
                focus.append(requirement.evidence_class)
            if selected:
                return selected, req_skipped, focus
        return [], [], focus

    def _team_completeness_status(
        team_id: str,
        team_mission: TeamMissionProfile,
        checklist: MissionChecklistResult,
        evidence_items: list[EvidenceItem],
        selected_tools: list[str],
        traces: list[AgentToolTrace],
        resolved_service: str | None,
    ) -> tuple[str, list[str]]:
        reasons: list[str] = [*checklist.unavailable, *checklist.failed]
        unmet_requirements = _unmet_evidence_requirements(team_mission, traces, resolved_service)
        if not checklist.passed:
            if team_id == "infra" and unmet_requirements:
                reasons.extend([f"missing_evidence_class:{item.evidence_class}" for item in unmet_requirements])
            if "missing_required_checks" not in reasons:
                reasons.append("missing_required_checks")
            return "unknown_not_available", _unique(reasons)
        if not evidence_items:
            reasons.append("no_evidence_collected")
            if team_id == "infra" and unmet_requirements:
                reasons.extend([f"missing_evidence_class:{item.evidence_class}" for item in unmet_requirements])
            return "unknown_not_available", _unique(reasons)

        if team_id == "infra" and unmet_requirements:
            reasons.extend([f"missing_evidence_class:{item.evidence_class}" for item in unmet_requirements])
            return "unknown_not_available", _unique(reasons)

        evidence_blob = "\n".join(_evidence_text(item).lower() for item in evidence_items[:8])
        unhealthy_tokens = ("error", "exception", "timeout", "oom", "memory", "cpu", "latency", "throttle", "fail")
        unhealthy = any(token in evidence_blob for token in unhealthy_tokens)
        if team_id == "infra" and not selected_tools:
            reasons.append("missing_infra_signals")
            return "unknown_not_available", reasons
        return ("unhealthy" if unhealthy else "healthy"), reasons

    def _execute_team(team: InvestigationTeamProfile) -> dict[str, Any]:
        local_timeline: list[str] = []
        local_execution_trace: list[dict[str, Any]] = []
        local_skipped_tools: list[dict[str, Any]] = []
        team_mission = _team_mission(run_context, tenant, environment, team.team_id)
        team_context_refs = _context_refs_for_scope(
            run_context=run_context,
            tenant=tenant,
            environment=environment,
            stage_id=WorkflowStageId.COLLECT_EVIDENCE,
            alert_payload=alert_payload,
            team_id=team.team_id,
            limit=8,
        )
        team_mission_id = f"team:{team.team_id}:{team_mission.updated_at.isoformat()}"
        base_observed_checks: set[str] = set()
        if team_context_refs:
            base_observed_checks.add("context_available")
        team_artifact_state = seeded_artifact_state.model_copy(deep=True)
        team_artifact_state, team_aliases = resolve_service_aliases(team_artifact_state)

        effective_allowlist = team.tool_allowlist or team_mission.allowed_tools
        allowlisted = [tool for tool in filter_tools_by_allowlist(mcp_tools, effective_allowlist) if tool.read_only]
        if team_mission.allowed_tools:
            allowlisted = [tool for tool in allowlisted if bool(filter_tools_by_allowlist([tool], team_mission.allowed_tools))]

        if not allowlisted:
            checklist = _evaluate_mission_checklist(
                mission_id=team_mission_id,
                required_checks=team_mission.required_checks,
                observed_checks=base_observed_checks,
                unavailable_candidates=set(team_mission.unknown_not_available_rules),
            )
            unknown_reasons = ["no_tools_matched_allowlist", *checklist.failed, *checklist.unavailable]
            summary = TeamExecutionSummary(
                team_id=team.team_id,
                status="skipped_no_tools",
                mission_id=team_mission_id,
                mission_checklist=checklist,
                context_refs=team_context_refs,
                unknown_not_available_reasons=unknown_reasons,
                relevance_weights=team_mission.relevance_weights,
                selected_tools=[],
                executed_tool_count=0,
                failed_tool_count=0,
                evidence_count=0,
                duration_ms=0,
                citations=[],
                error="No tools matched the team allowlist.",
                artifact_state=team_artifact_state,
                resolved_aliases=team_aliases,
                blocked_tools=[],
                invocable_tools=[],
            )
            draft = _build_team_draft(
                team_id=team.team_id,
                status="skipped_no_tools",
                objective_prompt=f"{team.objective_prompt}\nMission: {team_mission.mission_objective}",
                mission_id=team_mission_id,
                mission_checklist=checklist,
                context_refs=team_context_refs,
                relevance_weights=team_mission.relevance_weights,
                unknown_not_available_reasons=unknown_reasons,
                completeness_status="unknown_not_available",
                llm_route=llm_route,
                alert=alert,
                evidence_items=[],
                citations=[],
                tool_traces=[],
                skipped_tools=[],
                artifact_state=team_artifact_state,
                resolved_aliases=[item.model_dump(mode="json") for item in team_aliases],
                blocked_tools=[],
                invocable_tools=[],
                fallback_summary=f"{team.team_id} team skipped: no configured tools matched.",
            )
            local_timeline.append(f"Team {team.team_id} skipped: no tools matched allowlist.")
            return {
                "team_execution": summary,
                "team_report": draft,
                "execution_trace": local_execution_trace,
                "skipped_tools": local_skipped_tools,
                "timeline": local_timeline,
            }

        team_skipped: list[dict[str, Any]] = []
        team_traces: list[AgentToolTrace] = []
        team_evidence: list[EvidenceItem] = []
        selected_fqdns: list[str] = []
        requirement_focus_history: list[str] = []
        started_at = datetime.now(timezone.utc)
        remaining_budget = max(1, team.max_tool_calls)
        turn = 0

        while remaining_budget > 0:
            turn += 1
            team_context = derive_argument_context(alert_payload, service_identity_payload, team_artifact_state)
            invocable_before = invocable_tool_names(allowlisted, team_artifact_state)
            blocked_before = blocked_tool_entries(allowlisted, team_artifact_state)

            selected: list[Any] = []
            turn_skipped: list[dict[str, Any]] = []
            focused_requirements: list[str] = []

            rerun_focus = stage_rerun_directive.get("tool_focus") if isinstance(stage_rerun_directive, dict) else []
            if isinstance(rerun_focus, list) and rerun_focus:
                focused_tools = [
                    tool for tool in allowlisted
                    if f"mcp.{tool.server_id}.{tool.tool_name}" in rerun_focus
                ]
                if focused_tools:
                    selected, focus_skipped = select_mcp_tools(
                        focused_tools,
                        team_context,
                        allowlist=effective_allowlist,
                        max_tools=max(1, min(team.max_parallel_calls, remaining_budget)),
                        mode="evidence",
                        light_probe_only=False,
                        artifact_state=team_artifact_state,
                        alert_payload=alert_payload,
                    )
                    turn_skipped.extend(focus_skipped)

            if not selected:
                selected, turn_skipped, focused_requirements = _priority_requirement_tools(
                    team_mission,
                    allowlisted,
                    team_context,
                    team_artifact_state,
                    effective_allowlist,
                    team_traces,
                    team.max_parallel_calls,
                    remaining_budget,
                )
                requirement_focus_history.extend(focused_requirements)
            if not selected:
                selected, generic_skipped = select_mcp_tools(
                    allowlisted,
                    team_context,
                    allowlist=effective_allowlist,
                    max_tools=max(1, min(team.max_parallel_calls, remaining_budget)),
                    mode="evidence",
                    light_probe_only=False,
                    artifact_state=team_artifact_state,
                    alert_payload=alert_payload,
                )
                turn_skipped.extend(generic_skipped)
            if not selected:
                selected, discovery_skipped = select_mcp_tools(
                    allowlisted,
                    team_context,
                    allowlist=effective_allowlist,
                    max_tools=max(1, min(team.max_parallel_calls, remaining_budget)),
                    mode="discovery",
                    light_probe_only=True,
                    artifact_state=team_artifact_state,
                    alert_payload=alert_payload,
                )
                turn_skipped.extend(discovery_skipped)

            for item in turn_skipped:
                team_skipped.append({**item, "team_id": team.team_id, "turn": turn})

            if not selected:
                local_timeline.append(
                    f"Team {team.team_id} stopped after turn {turn}: no invocable tools. "
                    f"blocked={len(blocked_before)}."
                )
                break

            state_before = team_artifact_state.model_dump(mode="json")
            turn_new_evidence = 0
            futures: dict[Any, tuple[McpToolDescriptor, dict[str, Any]]] = {}
            batch_tools = selected[: max(1, min(team.max_parallel_calls, len(selected)))]

            with ThreadPoolExecutor(max_workers=max(1, len(batch_tools))) as executor:
                for planned in batch_tools:
                    future = executor.submit(_execute_mcp_tool, servers, planned.descriptor, planned.arguments)
                    futures[future] = (planned.descriptor, planned.arguments)

                done, pending = wait(set(futures.keys()), timeout=team.timeout_seconds)
                for future in done:
                    descriptor, _ = futures[future]
                    try:
                        result, trace = future.result()
                    except Exception as exc:
                        now = datetime.now(timezone.utc)
                        trace = AgentToolTrace(
                            tool_name=f"mcp.{descriptor.server_id}.{descriptor.tool_name}",
                            source="mcp",
                            read_only=True,
                            started_at=now,
                            ended_at=now,
                            duration_ms=0,
                            success=False,
                            args_summary={},
                            result_summary={"error": str(exc)},
                            error=str(exc),
                            citations=[],
                        )
                        result = {"error": str(exc)}
                    team_traces.append(trace)
                    selected_fqdns.append(trace.tool_name)
                    if not trace.success:
                        local_skipped_tools.append(
                            {
                                "tool_name": trace.tool_name,
                                "reason": "execution_error",
                                "error": trace.error,
                                "team_id": team.team_id,
                                "turn": turn,
                            }
                        )
                        continue

                    team_artifact_state = merge_artifact_state(
                        team_artifact_state,
                        extract_artifact_update(descriptor, result),
                    )
                    for signal in _extract_signals(result):
                        evidence = evidence_store.add(
                            investigation_id=investigation_id,
                            provider=descriptor.server_id,
                            evidence_type=descriptor.tool_name,
                            payload=signal,
                        )
                        policy_service.validate_redaction_state(evidence.redaction_state)
                        team_evidence.append(evidence)
                        trace.citations.append(evidence.citation_id)
                        turn_new_evidence += 1

                for future in pending:
                    descriptor, arguments = futures[future]
                    future.cancel()
                    now = datetime.now(timezone.utc)
                    timeout_trace = AgentToolTrace(
                        tool_name=f"mcp.{descriptor.server_id}.{descriptor.tool_name}",
                        source="mcp",
                        read_only=True,
                        started_at=now,
                        ended_at=now,
                        duration_ms=team.timeout_seconds * 1000,
                        success=False,
                        args_summary=arguments,
                        result_summary={"error": "team_timeout"},
                        error=f"Team timeout ({team.timeout_seconds}s) while executing tool.",
                        citations=[],
                    )
                    team_traces.append(timeout_trace)
                    selected_fqdns.append(timeout_trace.tool_name)
                    local_skipped_tools.append(
                        {
                            "tool_name": timeout_trace.tool_name,
                            "reason": "team_timeout",
                            "error": timeout_trace.error,
                            "team_id": team.team_id,
                            "turn": turn,
                        }
                    )

            team_artifact_state, team_aliases = resolve_service_aliases(team_artifact_state)
            remaining_budget -= len(batch_tools)
            local_execution_trace.extend([trace.model_dump(mode="json") for trace in team_traces[-len(batch_tools) :]])
            local_timeline.append(
                f"Team {team.team_id} turn {turn}: invocable_before={len(invocable_before)}, "
                f"executed={len(batch_tools)}, new_evidence={turn_new_evidence}."
            )

            if team_artifact_state.model_dump(mode="json") == state_before:
                local_timeline.append(
                    f"Team {team.team_id} stopped after turn {turn}: no new artifact discoveries."
                )
                break

        final_blocked_tools = blocked_tool_entries(allowlisted, team_artifact_state)
        final_invocable_tools = invocable_tool_names(allowlisted, team_artifact_state)
        local_skipped_tools.extend(team_skipped)

        if not team_traces and not team_evidence:
            checklist = _evaluate_mission_checklist(
                mission_id=team_mission_id,
                required_checks=team_mission.required_checks,
                observed_checks=base_observed_checks,
                unavailable_candidates=set(team_mission.unknown_not_available_rules),
            )
            unknown_reasons = ["no_invocable_tools", *checklist.failed, *checklist.unavailable]
            summary = TeamExecutionSummary(
                team_id=team.team_id,
                status="skipped_no_tools",
                mission_id=team_mission_id,
                mission_checklist=checklist,
                context_refs=team_context_refs,
                unknown_not_available_reasons=unknown_reasons,
                relevance_weights=team_mission.relevance_weights,
                selected_tools=[],
                executed_tool_count=0,
                failed_tool_count=0,
                evidence_count=0,
                duration_ms=0,
                citations=[],
                error="No invocable tools for current incident context.",
                artifact_state=team_artifact_state,
                resolved_aliases=team_aliases,
                blocked_tools=final_blocked_tools,
                invocable_tools=final_invocable_tools,
            )
            draft = _build_team_draft(
                team_id=team.team_id,
                status="skipped_no_tools",
                objective_prompt=f"{team.objective_prompt}\nMission: {team_mission.mission_objective}",
                mission_id=team_mission_id,
                mission_checklist=checklist,
                context_refs=team_context_refs,
                relevance_weights=team_mission.relevance_weights,
                unknown_not_available_reasons=unknown_reasons,
                completeness_status="unknown_not_available",
                llm_route=llm_route,
                alert=alert,
                evidence_items=[],
                citations=[],
                tool_traces=[],
                skipped_tools=team_skipped,
                artifact_state=team_artifact_state,
                resolved_aliases=[item.model_dump(mode="json") for item in team_aliases],
                blocked_tools=final_blocked_tools,
                invocable_tools=final_invocable_tools,
                fallback_summary=f"{team.team_id} team skipped: no invocable tools for current context.",
            )
            return {
                "team_execution": summary,
                "team_report": draft,
                "execution_trace": local_execution_trace,
                "skipped_tools": local_skipped_tools,
                "timeline": local_timeline,
            }

        completed_at = datetime.now(timezone.utc)
        citations = [item.citation_id for item in team_evidence]
        observed_checks = set(base_observed_checks)
        observed_checks.update({f"tool:{name}" for name in selected_fqdns})
        if team_evidence:
            observed_checks.add("evidence_collected")
        if citations:
            observed_checks.add("citations_created")
        if any(token in name for name in selected_fqdns for token in ("service_operations", "get_operations")):
            observed_checks.add("service_operations_reviewed")
        if any("jaeger" in name for name in selected_fqdns):
            observed_checks.add("trace_errors_checked")
        if any("annotation" in name for name in selected_fqdns):
            observed_checks.add("infra_annotations_checked")
        if any(
            token in name
            for name in selected_fqdns
            for token in (
                "error",
                "slow",
                "latency",
                "cpu",
                "memory",
                "query_prometheus",
                "query_range",
                "query_instant",
                "query_loki",
                "search_dashboards",
            )
        ):
            observed_checks.add("infra_latency_or_error_signal_checked")
        for requirement in _required_evidence_requirements(team_mission):
            if _requirement_satisfied(requirement, team_traces, team_artifact_state.resolved_service):
                observed_checks.add(requirement.evidence_class)

        checklist = _evaluate_mission_checklist(
            mission_id=team_mission_id,
            required_checks=team_mission.required_checks,
            observed_checks=observed_checks,
            unavailable_candidates=set(),
        )
        completeness_status, unknown_reasons = _team_completeness_status(
            team.team_id,
            team_mission,
            checklist,
            team_evidence,
            selected_fqdns,
            team_traces,
            team_artifact_state.resolved_service,
        )

        summary = TeamExecutionSummary(
            team_id=team.team_id,
            status="completed",
            mission_id=team_mission_id,
            mission_checklist=checklist,
            context_refs=team_context_refs,
            unknown_not_available_reasons=unknown_reasons,
            relevance_weights=team_mission.relevance_weights,
            selected_tools=selected_fqdns,
            executed_tool_count=len(team_traces),
            failed_tool_count=sum(1 for trace in team_traces if not trace.success),
            evidence_count=len(team_evidence),
            duration_ms=int((completed_at - started_at).total_seconds() * 1000),
            citations=citations,
            error=None,
            artifact_state=team_artifact_state,
            resolved_aliases=team_aliases,
            blocked_tools=final_blocked_tools,
            invocable_tools=final_invocable_tools,
        )
        draft = _build_team_draft(
            team_id=team.team_id,
            status="completed",
            objective_prompt=f"{team.objective_prompt}\nMission: {team_mission.mission_objective}",
            mission_id=team_mission_id,
            mission_checklist=checklist,
            context_refs=team_context_refs,
            relevance_weights=team_mission.relevance_weights,
            unknown_not_available_reasons=unknown_reasons,
            completeness_status=completeness_status,
            llm_route=llm_route,
            alert=alert,
            evidence_items=team_evidence,
            citations=citations,
            tool_traces=team_traces,
            skipped_tools=team_skipped,
            artifact_state=team_artifact_state,
            resolved_aliases=[item.model_dump(mode="json") for item in team_aliases],
            blocked_tools=final_blocked_tools,
            invocable_tools=final_invocable_tools,
            fallback_summary=f"{team.team_id} team completed evidence review.",
        )
        if requirement_focus_history:
            local_timeline.append(
                f"Team {team.team_id} requirement focus: {', '.join(_unique(requirement_focus_history))}."
            )

        local_timeline.append(
            f"Team {team.team_id} completed: tools={len(team_traces)}, evidence={len(team_evidence)}, "
            f"failures={summary.failed_tool_count}, completeness={completeness_status}."
        )
        return {
            "team_execution": summary,
            "team_report": draft,
            "execution_trace": local_execution_trace,
            "skipped_tools": local_skipped_tools,
            "timeline": local_timeline,
        }

    if teams:
        max_team_workers = max(1, min(len(teams), 6))
        with ThreadPoolExecutor(max_workers=max_team_workers) as team_executor:
            team_futures = {team_executor.submit(_execute_team, team): idx for idx, team in enumerate(teams)}
            ordered_results: list[tuple[int, dict[str, Any]]] = []
            for future, idx in team_futures.items():
                team = teams[idx]
                try:
                    team_result = future.result()
                except Exception as exc:
                    team_mission = _team_mission(run_context, tenant, environment, team.team_id)
                    team_mission_id = f"team:{team.team_id}:{team_mission.updated_at.isoformat()}"
                    checklist = _evaluate_mission_checklist(
                        mission_id=team_mission_id,
                        required_checks=team_mission.required_checks,
                        observed_checks=set(),
                        unavailable_candidates=set(team_mission.unknown_not_available_rules),
                    )
                    failure_summary = TeamExecutionSummary(
                        team_id=team.team_id,
                        status="failed",
                        mission_id=team_mission_id,
                        mission_checklist=checklist,
                        context_refs=[],
                        unknown_not_available_reasons=["team_failure", *checklist.failed, *checklist.unavailable],
                        relevance_weights=team_mission.relevance_weights,
                        selected_tools=[],
                        executed_tool_count=0,
                        failed_tool_count=1,
                        evidence_count=0,
                        duration_ms=0,
                        citations=[],
                        error=str(exc),
                        artifact_state=seeded_artifact_state,
                        resolved_aliases=seeded_artifact_state.service_aliases,
                        blocked_tools=[],
                        invocable_tools=[],
                    )
                    failure_report = TeamRcaDraft(
                        team_id=team.team_id,
                        status="failed",
                        summary=f"{team.team_id} team execution failed.",
                        mission_id=team_mission_id,
                        mission_checklist=checklist,
                        context_refs=[],
                        unknown_not_available_reasons=["team_failure"],
                        relevance_weights=team_mission.relevance_weights,
                        completeness_status="unknown_not_available",
                        hypotheses=[],
                        confidence=0.0,
                        supporting_citations=[],
                        unknowns=[str(exc)],
                        tool_traces=[],
                        skipped_tools=[],
                        artifact_state=seeded_artifact_state,
                        resolved_aliases=seeded_artifact_state.service_aliases,
                        blocked_tools=[],
                        invocable_tools=[],
                    )
                    team_result = {
                        "team_execution": failure_summary,
                        "team_report": failure_report,
                        "execution_trace": [],
                        "skipped_tools": [{"team_id": team.team_id, "reason": "team_failure", "error": str(exc)}],
                        "timeline": [f"Team {team.team_id} failed: {exc}"],
                    }
                ordered_results.append((idx, team_result))

        for _, team_result in sorted(ordered_results, key=lambda item: item[0]):
            team_execution.append(team_result["team_execution"])
            team_reports.append(team_result["team_report"])
            execution_trace.extend(team_result["execution_trace"])
            skipped_tools.extend(team_result["skipped_tools"])
            timeline.extend(team_result["timeline"])

    stage_artifact_state = seeded_artifact_state.model_copy(deep=True)
    stage_resolved_aliases = list(stage_artifact_state.service_aliases)
    stage_blocked_tools: list[dict[str, Any]] = []
    stage_invocable_tools: list[str] = []
    for summary in team_execution:
        if summary.artifact_state:
            stage_artifact_state = merge_artifact_state(stage_artifact_state, summary.artifact_state)
        stage_blocked_tools.extend(summary.blocked_tools)
        stage_invocable_tools.extend(summary.invocable_tools)
        for alias in summary.resolved_aliases:
            if alias not in stage_resolved_aliases:
                stage_resolved_aliases.append(alias)
    stage_artifact_state, stage_aliases = resolve_service_aliases(stage_artifact_state)
    if stage_aliases:
        stage_resolved_aliases = stage_aliases

    evidence_items = evidence_store.list(investigation_id)
    if not evidence_items:
        fallback_evidence = evidence_store.add(
            investigation_id=investigation_id,
            provider="mcp",
            evidence_type="alert-context",
            payload={"incident_key": alert.incident_key, "raw_payload_ref": alert.raw_payload_ref},
        )
        policy_service.validate_redaction_state(fallback_evidence.redaction_state)
        evidence_items = [fallback_evidence]
        timeline.append("No team evidence collected; used alert-context fallback evidence.")

    executed_steps = sum(item.executed_tool_count for item in team_execution)
    stage_observed_checks: set[str] = set()
    if team_execution:
        stage_observed_checks.add("team_execution_completed")
    if evidence_items:
        stage_observed_checks.add("evidence_items_collected")
    if any(item.citation_id for item in evidence_items):
        stage_observed_checks.add("citations_created")
    if team_reports:
        stage_observed_checks.add("team_reports_produced")

    stage_unavailable_candidates: set[str] = set()
    if not teams:
        stage_unavailable_candidates.add("team_no_tools")
    checklist = _evaluate_mission_checklist(
        mission_id=stage_mission_id,
        required_checks=stage_mission.required_checks,
        observed_checks=stage_observed_checks,
        unavailable_candidates=stage_unavailable_candidates,
    )
    unknown_reasons = [*checklist.unavailable, *checklist.failed]
    stage_reasoning = (
        f"Team-agent evidence collection completed across {len(team_execution)} team(s), "
        f"executed {executed_steps} tool call(s), produced {len(evidence_items)} evidence item(s). "
        f"Mission objective: {stage_mission.mission_objective}. Context refs used: {len(stage_context_refs)}. "
        f"Resolved service={stage_artifact_state.resolved_service or 'unknown'}."
    )
    infra_summary = next((item for item in team_execution if item.team_id == "infra"), None)
    infra_findings: list[str] = []
    infra_status = "pass"
    if infra_summary:
        if infra_summary.unknown_not_available_reasons:
            infra_status = "warn"
            infra_findings.extend(infra_summary.unknown_not_available_reasons)
        if infra_summary.status == "failed":
            infra_status = "fail"
            infra_findings.append("infra_team_failed")
    else:
        infra_status = "warn"
        infra_findings.append("infra_team_missing")
    payload = {
        "executed_steps": executed_steps,
        "stopped_early": False,
        "timeline": timeline,
        "execution_trace": execution_trace,
        "skipped_tools": skipped_tools,
        "evidence": [item.model_dump(mode="json") for item in evidence_items],
        "team_reports": [item.model_dump(mode="json") for item in team_reports],
        "team_execution": [item.model_dump(mode="json") for item in team_execution],
        "stage_reasoning_summary": stage_reasoning,
        "artifact_state": stage_artifact_state.model_dump(mode="json"),
        "resolved_aliases": [item.model_dump(mode="json") for item in stage_resolved_aliases],
        "alias_decision_trace": (
            stage_artifact_state.alias_decision_trace.model_dump(mode="json")
            if stage_artifact_state.alias_decision_trace
            else None
        ),
        "blocked_tools": stage_blocked_tools,
        "invocable_tools": sorted(set(stage_invocable_tools)),
        "effective_mission_snapshot": stage_mission.model_dump(mode="json"),
        "effective_team_mission_snapshots": {
            team.team_id: _team_mission(run_context, tenant, environment, team.team_id).model_dump(mode="json")
            for team in teams
        },
        "effective_tool_catalog_summary": _effective_tool_catalog_summary(mcp_tools, stage_mission.allowed_tools),
        "rerun_directives": [],
        "stage_eval_records": [
            _stage_eval_record(
                stage_id=WorkflowStageId.COLLECT_EVIDENCE,
                record_id="infra_completeness",
                status=infra_status,
                summary=(
                    "Infra evidence satisfied required evidence classes."
                    if infra_status == "pass"
                    else "Infra evidence is incomplete or unavailable for a negative-proof conclusion."
                ),
                score=1.0 if infra_status == "pass" else (0.6 if infra_status == "warn" else 0.2),
                findings=_unique(infra_findings),
                details=infra_summary.model_dump(mode="json") if infra_summary else {},
            )
        ],
    }
    payload.update(
        _mission_metadata(
            mission_id=stage_mission_id,
            mission_checklist=checklist,
            context_refs=stage_context_refs,
            relevance_weights=stage_mission.relevance_weights,
            unknown_not_available_reasons=unknown_reasons,
        )
    )
    return payload


def _evidence_text(item: EvidenceItem) -> str:
    """Unwrap MCP content envelope and present evidence compactly.

    MCP tools return {"content": [{"type": "text", "text": "...JSON..."}]}.
    For span lists (get_trace), sort erroring spans first so the synthesis
    LLM always sees the most relevant evidence within the token budget.
    """
    fields = item.normalized_fields
    content = fields.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "text":
            raw = first.get("text", "")
            try:
                inner = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return raw[:3000]
            if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                # Span list: sort errors first, keep top 12, drop noisy keys
                _KEEP = {"operationName", "service", "error", "duration_us", "tags"}

                def _slim(s: dict) -> dict:
                    slim = {k: v for k, v in s.items() if k in _KEEP}
                    if isinstance(slim.get("tags"), dict):
                        slim["tags"] = {
                            k: v for k, v in slim["tags"].items()
                            if any(tok in k for tok in ("error", "status", "grpc", "http", "app.", "exception"))
                        }
                    return slim

                erroring = [_slim(s) for s in inner if s.get("error")]
                ok = [_slim(s) for s in inner if not s.get("error")]
                ordered = erroring + ok
                return json.dumps(ordered[:12], indent=2)[:4000]
            return json.dumps(inner)[:3000]
    return json.dumps(fields)[:3000]


def synthesize_report_stage(
    alert_payload: dict[str, Any],
    service_identity_payload: dict[str, Any],
    evidence_payload: list[dict[str, Any]],
    run_context: dict[str, Any] | None = None,
    collect_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ = AlertEnvelope.model_validate(alert_payload)
    service_identity = ServiceIdentity.model_validate(service_identity_payload)
    evidence_items = [EvidenceItem.model_validate(item) for item in evidence_payload]
    collect_result = collect_result or {}
    raw_team_reports = collect_result.get("team_reports", [])
    team_reports = [TeamRcaDraft.model_validate(item) for item in raw_team_reports if isinstance(item, dict)]
    raw_team_execution = collect_result.get("team_execution", [])
    team_execution = [TeamExecutionSummary.model_validate(item) for item in raw_team_execution if isinstance(item, dict)]
    team_weight_scores: dict[str, float] = {}
    for report in team_reports:
        service_weight = float(report.relevance_weights.get("service_scoped", 1.0))
        global_weight = float(report.relevance_weights.get("global", 0.5))
        team_weight_scores[report.team_id] = round(report.confidence * ((service_weight + global_weight) / 2), 4)

    citations_by_provider: defaultdict[str, list[str]] = defaultdict(list)
    for item in evidence_items:
        citations_by_provider[item.provider].append(item.citation_id)

    provider_rank = sorted(citations_by_provider.items(), key=lambda kv: len(kv[1]), reverse=True)
    ranking_trace = [
        {
            "provider": provider,
            "citation_count": len(citations),
            "sample_citations": citations[:3],
        }
        for provider, citations in provider_rank
    ]
    tenant, environment = _tenant_and_environment(run_context)
    llm_route = _llm_route(run_context, tenant, environment)
    stage_mission = _stage_mission(run_context, tenant, environment, WorkflowStageId.SYNTHESIZE_RCA_REPORT)
    context_refs = _context_refs_for_scope(
        run_context=run_context,
        tenant=tenant,
        environment=environment,
        stage_id=WorkflowStageId.SYNTHESIZE_RCA_REPORT,
        alert_payload=alert_payload,
        limit=8,
    )
    stage_mission_id = f"stage:{WorkflowStageId.SYNTHESIZE_RCA_REPORT.value}:{stage_mission.updated_at.isoformat()}"
    route = _agent_route_from_llm(llm_route)
    alert_obj = AlertEnvelope.model_validate(alert_payload)

    evidence_digest = "\n\n".join(
        f"[{item.provider.upper()} / {item.evidence_type}] {item.citation_id}\n" + _evidence_text(item)
        for item in evidence_items[:10]
    )
    team_digest = "\n".join(
        (
            f"- team={report.team_id} status={report.status} confidence={report.confidence:.2f} "
            f"weight_score={team_weight_scores.get(report.team_id, 0.0):.2f} "
            f"citations={len(report.supporting_citations)} summary={report.summary}"
        )
        for report in team_reports
    )
    context_digest = "\n".join(f"- {item.summary} ({item.context_citation_id})" for item in context_refs[:6])
    synthesis_prompt = (
        f"You are the commander agent for service '{service_identity.canonical_service_id}'.\n\n"
        f"Mission objective: {stage_mission.mission_objective}\n"
        f"ALERT: {json.dumps(alert_obj.raw_payload)}\n\n"
        f"CONTEXT REFS ({len(context_refs)}):\n{context_digest or '- none'}\n\n"
        f"TEAM REPORTS ({len(team_reports)}):\n{team_digest or '- none'}\n\n"
        f"EVIDENCE ({len(evidence_items)} items):\n{evidence_digest}\n\n"
        "Tasks:\n"
        "1. Arbitrate team findings and identify likely root cause.\n"
        "2. Output 1-3 concise hypotheses prefixed H1/H2/H3.\n"
        "3. Output CAUSE, ACTIONS, CONFLICTS, DECISION_TRACE, SELECTED_TEAMS.\n"
        "4. If investigation must be re-run, append:\n"
        "RERUN_STAGE: <resolve_service_identity|build_investigation_plan|collect_evidence>\n"
        "RERUN_REASON: <why more investigation is needed>\n"
        "RERUN_OBJECTIVE: <what the rerun should discover>\n"
        "RERUN_EVIDENCE: <missing artifact or evidence class>\n"
        "RERUN_TOOL_FOCUS: <comma-separated tool names>\n"
        "Format:\n"
        "H1: ...\nH2: ...\nH3: ...\nCAUSE: ...\nACTIONS: a | b | c\n"
        "CONFLICTS: item1 | item2\nDECISION_TRACE: ...\nSELECTED_TEAMS: app,infra"
    )

    model_used, llm_summary = summarize_with_model_route(
        route,
        synthesis_prompt,
        system_prompt="You are a precise incident commander. Reconcile team reports with evidence citations.",
        max_tokens=900,
    )

    # Parse LLM output into structured fields
    hypothesis_statements: list[str] = []
    likely_cause = "Unable to determine root cause."
    selected_teams: list[str] = []
    arbitration_conflicts: list[str] = []
    arbitration_decision_trace = "Commander arbitration used evidence-backed ranking."
    recommended_actions = [
        "Validate recent deploys/config changes for impacted service and dependencies.",
        "Correlate logs/traces around incident window and confirm rollback criteria.",
        "Escalate to service owner for manual mitigation if customer impact persists.",
    ]
    for line in llm_summary.splitlines():
        line = line.strip()
        if line.startswith(("H1:", "H2:", "H3:")):
            hypothesis_statements.append(line[3:].strip())
        elif line.startswith("CAUSE:"):
            likely_cause = line[6:].strip()
        elif line.startswith("ACTIONS:"):
            parts = [a.strip() for a in line[8:].split("|") if a.strip()]
            if parts:
                recommended_actions = parts
        elif line.startswith("CONFLICTS:"):
            arbitration_conflicts = [item.strip() for item in line[10:].split("|") if item.strip()]
        elif line.startswith("DECISION_TRACE:"):
            arbitration_decision_trace = line[15:].strip() or arbitration_decision_trace
        elif line.startswith("SELECTED_TEAMS:"):
            selected_teams = [item.strip() for item in line[15:].split(",") if item.strip()]
    if not selected_teams and team_weight_scores:
        selected_teams = [
            item[0]
            for item in sorted(team_weight_scores.items(), key=lambda kv: kv[1], reverse=True)[:3]
        ]

    team_citations: list[str] = []
    for report in team_reports:
        for citation in report.supporting_citations:
            if citation not in team_citations:
                team_citations.append(citation)

    # Build hypotheses: commander statements with team-backed citations when available.
    hypotheses: list[Hypothesis] = []
    for idx, (provider, citations) in enumerate(provider_rank[:3]):
        statement = (
            hypothesis_statements[idx]
            if idx < len(hypothesis_statements)
            else f"Evidence from {provider} indicates a degradation pattern for {service_identity.canonical_service_id}."
        )
        supporting = team_citations[:3] if team_citations else citations[:3]
        hypotheses.append(
            Hypothesis(
                statement=statement,
                confidence=max(0.45, 0.82 - (idx * 0.14)),
                supporting_citations=supporting,
                counter_evidence_citations=[],
            )
        )

    if not hypotheses:
        hypotheses.append(
            Hypothesis(
                statement=likely_cause,
                confidence=0.5,
                supporting_citations=[c for _, cits in provider_rank[:1] for c in cits[:1]],
                counter_evidence_citations=[],
            )
        )

    enforce_citation_policy(hypotheses)

    report = RcaReport(
        top_hypotheses=hypotheses,
        likely_cause=likely_cause,
        blast_radius=f"Primary impact on {service_identity.canonical_service_id} and direct dependencies.",
        recommended_manual_actions=recommended_actions,
        confidence=round(sum(h.confidence for h in hypotheses) / len(hypotheses), 2),
    )
    arbitration = CommanderArbitrationSummary(
        selected_team_ids=selected_teams,
        arbitration_conflicts=arbitration_conflicts,
        arbitration_decision_trace=arbitration_decision_trace,
    )
    parsed_reruns = _parse_rerun_directives(
        llm_summary,
        allowed_targets={
            WorkflowStageId.RESOLVE_SERVICE_IDENTITY,
            WorkflowStageId.BUILD_INVESTIGATION_PLAN,
            WorkflowStageId.COLLECT_EVIDENCE,
        },
    )
    collect_alias_trace_payload = collect_result.get("alias_decision_trace")
    collect_alias_trace = (
        AliasDecisionTrace.model_validate(collect_alias_trace_payload)
        if isinstance(collect_alias_trace_payload, dict)
        else None
    )
    policy_reruns: list[RerunDirective] = []
    infra_summary = next((item for item in team_execution if item.team_id == "infra"), None)
    if not parsed_reruns:
        if collect_alias_trace and collect_alias_trace.matched_term_source == "summary":
            policy_reruns = [
                RerunDirective(
                    target_stage=WorkflowStageId.RESOLVE_SERVICE_IDENTITY,
                    reason="service_scope_suspect",
                    additional_objective=(
                        "Resolve the telemetry-native service strictly from explicit service anchors "
                        "before collecting more evidence."
                    ),
                    expected_evidence="anchored service resolution aligned to alert entity",
                    tool_focus=["mcp.jaeger.get_services", "mcp.prometheus.list_label_values"],
                )
            ]
        elif infra_summary and infra_summary.unknown_not_available_reasons:
            policy_reruns = [
                RerunDirective(
                    target_stage=WorkflowStageId.COLLECT_EVIDENCE,
                    reason="infra_evidence_incomplete",
                    additional_objective=(
                        "Collect the missing infra evidence classes before concluding the platform is healthy."
                    ),
                    expected_evidence="local and global Prometheus metric checks plus annotation context",
                    tool_focus=["mcp.prometheus.query_range", "mcp.prometheus.query_instant", "mcp.grafana.get_annotations"],
                )
            ]
        elif not evidence_items:
            policy_reruns = [
                RerunDirective(
                    target_stage=WorkflowStageId.BUILD_INVESTIGATION_PLAN,
                    reason="insufficient_evidence",
                    additional_objective="Re-plan around missing evidence classes and service-scoped tools.",
                    expected_evidence="service-scoped metrics or traces",
                    tool_focus=["mcp.jaeger.search_traces", "mcp.prometheus.query_range"],
                )
            ]
    rerun_directives = parsed_reruns or policy_reruns
    observed_checks: set[str] = set()
    if len(hypotheses) <= 3 and hypotheses:
        observed_checks.add("top3_generated")
    if all(item.supporting_citations for item in hypotheses):
        observed_checks.add("citations_attached")
    if report.likely_cause:
        observed_checks.add("likely_cause_present")
    if report.recommended_manual_actions:
        observed_checks.add("manual_actions_present")
    checklist = _evaluate_mission_checklist(
        mission_id=stage_mission_id,
        required_checks=stage_mission.required_checks,
        observed_checks=observed_checks,
        unavailable_candidates=set(),
    )
    unknown_reasons = [*checklist.unavailable, *checklist.failed]

    payload = {
        "report": report.model_dump(mode="json"),
        "hypotheses": [h.model_dump(mode="json") for h in hypotheses],
        "llm_model_used": model_used,
        "llm_summary": llm_summary,
        "requested_model": {"primary": llm_route.primary_model, "fallback": llm_route.fallback_model},
        "resolved_model": {
            "primary": resolve_model_alias(llm_route.primary_model),
            "fallback": resolve_model_alias(llm_route.fallback_model),
            "used": model_used,
        },
        "model_error": None,
        "team_reports": [item.model_dump(mode="json") for item in team_reports],
        "team_execution": [item.model_dump(mode="json") for item in team_execution],
        "arbitration_conflicts": arbitration.arbitration_conflicts,
        "arbitration_decision_trace": arbitration.arbitration_decision_trace,
        "synthesis_trace": {
            "service": service_identity.canonical_service_id,
            "evidence_count": len(evidence_items),
            "provider_ranking": ranking_trace,
            "selected_team_ids": arbitration.selected_team_ids,
            "arbitration_conflicts": arbitration.arbitration_conflicts,
            "team_weight_scores": team_weight_scores,
        },
        "rerun_directives": [item.model_dump(mode="json") for item in rerun_directives],
        "effective_mission_snapshot": stage_mission.model_dump(mode="json"),
        "stage_eval_records": [
            _stage_eval_record(
                stage_id=WorkflowStageId.SYNTHESIZE_RCA_REPORT,
                record_id="final_rca_quality",
                status=(
                    "pass"
                    if len(report.top_hypotheses) <= 3 and all(item.supporting_citations for item in report.top_hypotheses)
                    else "fail"
                ),
                summary="Final RCA quality evaluated from hypothesis count, citations, and abstention signals.",
                score=round(sum(item.confidence for item in report.top_hypotheses) / max(1, len(report.top_hypotheses)), 2),
                findings=[*arbitration_conflicts, *([] if not rerun_directives else ["rerun_requested"])],
                details={
                    "likely_cause": report.likely_cause,
                    "hypothesis_count": len(report.top_hypotheses),
                    "rerun_directives": [item.model_dump(mode="json") for item in rerun_directives],
                },
            )
        ],
    }
    payload.update(
        _mission_metadata(
            mission_id=stage_mission_id,
            mission_checklist=checklist,
            context_refs=context_refs,
            relevance_weights=stage_mission.relevance_weights,
            unknown_not_available_reasons=unknown_reasons,
        )
    )
    return payload


def publish_stage(alert_payload: dict[str, Any], report_payload: dict[str, Any], enabled: bool = True) -> dict[str, Any]:
    alert = AlertEnvelope.model_validate(alert_payload)
    report = RcaReport.model_validate(report_payload)

    if not enabled:
        return {
            "published": False,
            "slack_message_id": None,
            "jira_issue_key": None,
            "publish_trace": {"slack_enabled": False, "jira_enabled": False, "mode": "skipped"},
        }

    publish_result = Publisher().publish(report=report, incident_key=alert.incident_key)
    return {
        "published": True,
        "slack_message_id": publish_result.slack_message_id,
        "jira_issue_key": publish_result.jira_issue_key,
        "publish_trace": {
            "slack_enabled": True,
            "jira_enabled": True,
            "mode": "stubbed",
            "incident_key": alert.incident_key,
        },
    }


def emit_eval_event_stage(
    investigation_id: str,
    report_payload: dict[str, Any],
    evidence_payload: list[dict[str, Any]],
    latency_seconds: float,
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = RcaReport.model_validate(report_payload)
    evidence = [EvidenceItem.model_validate(item) for item in evidence_payload]
    tenant, environment = _tenant_and_environment(run_context)
    stage_mission = _stage_mission(run_context, tenant, environment, WorkflowStageId.EMIT_EVAL_EVENT)
    context_refs = _context_refs_for_scope(
        run_context=run_context,
        tenant=tenant,
        environment=environment,
        stage_id=WorkflowStageId.EMIT_EVAL_EVENT,
        alert_payload={"incident_key": investigation_id, "entity_ids": []},
        limit=5,
    )
    stage_mission_id = f"stage:{WorkflowStageId.EMIT_EVAL_EVENT.value}:{stage_mission.updated_at.isoformat()}"
    citation_count = sum(len(h.supporting_citations) for h in report.top_hypotheses)
    stage_results = (run_context or {}).get("stage_results", {})
    effective_prompt_profiles = (run_context or {}).get("effective_prompt_profiles", {})
    effective_stage_missions = (run_context or {}).get("effective_stage_missions", {})
    effective_team_missions = (run_context or {}).get("effective_team_missions", {})
    rerun_ledger = (run_context or {}).get("rerun_ledger", [])
    alias_decision_trace = (run_context or {}).get("alias_decision_trace")
    stage_eval_records = (run_context or {}).get("stage_eval_records", [])
    tool_catalog_summary = {
        stage_id: payload.get("effective_tool_catalog_summary")
        for stage_id, payload in stage_results.items()
        if isinstance(payload, dict) and payload.get("effective_tool_catalog_summary")
    }
    observed_checks: set[str] = {"eval_payload_valid"}
    if latency_seconds >= 0:
        observed_checks.add("latency_emitted")
    if citation_count >= 0:
        observed_checks.add("citation_metrics_emitted")
    checklist = _evaluate_mission_checklist(
        mission_id=stage_mission_id,
        required_checks=stage_mission.required_checks,
        observed_checks=observed_checks,
        unavailable_candidates=set(),
    )

    payload = {
        "investigation_id": investigation_id,
        "top_hypothesis_count": len(report.top_hypotheses),
        "citation_count": citation_count,
        "evidence_count": len(evidence),
        "latency_seconds": round(latency_seconds, 2),
        "requires_human_review": True,
        "required_human_review_percent": 100,
        "rollout_mode": str((run_context or {}).get("agent_rollout_mode") or "compare"),
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "stage_eval_records": stage_eval_records,
        "rerun_ledger": rerun_ledger,
        "alias_decision_trace": alias_decision_trace,
        "effective_prompt_snapshot": effective_prompt_profiles,
        "effective_mission_snapshot": {
            "stages": effective_stage_missions,
            "teams": effective_team_missions,
        },
        "eval_trace": {
            "gate_metrics": {
                "top_hypothesis_count": len(report.top_hypotheses),
                "citation_count": citation_count,
                "evidence_count": len(evidence),
            },
            "latency_seconds": round(latency_seconds, 2),
            "training_artifact": {
                "investigation_id": investigation_id,
                "prompt_snapshot": effective_prompt_profiles,
                "mission_snapshot": {
                    "stages": effective_stage_missions,
                    "teams": effective_team_missions,
                },
                "tool_catalog_summary": tool_catalog_summary,
                "rerun_ledger": rerun_ledger,
                "alias_decision_trace": alias_decision_trace,
                "stage_eval_records": stage_eval_records,
                "context_ref_ids": [item.context_citation_id for item in context_refs],
                "adjudication_outcome": None,
            },
        },
    }
    payload.update(
        _mission_metadata(
            mission_id=stage_mission_id,
            mission_checklist=checklist,
            context_refs=context_refs,
            relevance_weights=stage_mission.relevance_weights,
            unknown_not_available_reasons=[*checklist.failed, *checklist.unavailable],
        )
    )
    return payload
