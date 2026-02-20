from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from connectors.core.azure.plugin import AzureConnector
from connectors.core.newrelic.plugin import NewRelicConnector
from connectors.core.otel.plugin import OTelConnector
from platform_core.llm_router import ModelRoute, synthesize_with_fallback
from platform_core.models import (
    AgentPromptProfile,
    AgentToolTrace,
    AlertEnvelope,
    InvestigationPlan,
    PlanStep,
    WorkflowStageId,
)
from platform_core.planner import build_default_plan
from platform_core.policy import enforce_budget_policy
from platform_core.resolver import resolve_service_identity
from platform_core.tool_registry import ToolDefinition, ToolRegistry


@dataclass
class AgentExecutionResult:
    payload: dict[str, Any]
    llm_model_used: str
    llm_summary: str
    stage_reasoning_summary: str
    tool_traces: list[AgentToolTrace]


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if any(token in lowered for token in ("secret", "token", "key", "password", "auth")):
                safe[key] = "***redacted***"
                continue
            if isinstance(item, (str, int, float, bool)) or item is None:
                safe[key] = item if not isinstance(item, str) else item[:200]
            elif isinstance(item, list):
                safe[key] = {"type": "list", "count": len(item)}
            elif isinstance(item, dict):
                safe[key] = {"type": "object", "keys": list(item.keys())[:12]}
            else:
                safe[key] = str(item)
        return safe
    if isinstance(value, list):
        return {"type": "list", "count": len(value)}
    if isinstance(value, str):
        return value[:200]
    return value


def _model_route_summary(route: ModelRoute, objective: str) -> tuple[str, str]:
    def _primary_call(model: str, prompt: str) -> str:
        if os.getenv("SIMULATE_PRIMARY_LLM_FAILURE") == "1":
            raise RuntimeError("simulated primary LLM failure")
        return f"{model}: {prompt}"

    def _fallback_call(model: str, prompt: str) -> str:
        return f"{model}: fallback -> {prompt}"

    return synthesize_with_fallback(route, _primary_call, _fallback_call, objective)


def _execute_tool(
    registry: ToolRegistry,
    tool: ToolDefinition,
    *,
    alert_payload: dict[str, Any],
    service_identity: dict[str, Any],
    plan_step: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], AgentToolTrace]:
    started = datetime.now(timezone.utc)
    timer = perf_counter()
    error: str | None = None
    result: dict[str, Any]
    args = {
        "alert": alert_payload,
        "service_identity": service_identity,
    }
    if plan_step is not None:
        args["plan_step"] = plan_step
    try:
        result = registry.call_tool(tool.name, args)
        success = True
    except Exception as exc:
        success = False
        error = str(exc)
        result = {"error": str(exc)}
    ended = datetime.now(timezone.utc)
    trace = AgentToolTrace(
        tool_name=tool.name,
        source=tool.source,
        read_only=tool.read_only,
        started_at=started,
        ended_at=ended,
        duration_ms=int((perf_counter() - timer) * 1000),
        success=success,
        args_summary=_sanitize_value(args),
        result_summary=_sanitize_value(result),
        error=error,
        citations=[],
    )
    return result, trace


def _default_connectors() -> list[Any]:
    return [NewRelicConnector(), AzureConnector(), OTelConnector()]


def _resolver_objective(profile: AgentPromptProfile, alert: AlertEnvelope) -> str:
    objective = profile.objective_template.replace("{{incident_key}}", alert.incident_key)
    return (
        f"{profile.system_prompt}\n"
        f"Objective: {objective}\n"
        "Constraints: read-only tools only, output canonical service identity and ambiguity.\n"
        "Goal: resolve the alert root-cause investigation context for manual remediation."
    )


def _planner_objective(profile: AgentPromptProfile, alert: AlertEnvelope) -> str:
    objective = profile.objective_template.replace("{{incident_key}}", alert.incident_key)
    return (
        f"{profile.system_prompt}\n"
        f"Objective: {objective}\n"
        "Constraints: planning stage allows light probes only; do not fetch deep evidence.\n"
        "Goal: produce a bounded investigation plan that helps fix the alert manually."
    )


def run_resolver_agent(
    *,
    alert_payload: dict[str, Any],
    model_route: ModelRoute,
    prompt_profile: AgentPromptProfile,
    mcp_servers: list[Any] | None = None,
    mcp_tools: list[Any] | None = None,
) -> AgentExecutionResult:
    alert = AlertEnvelope.model_validate(alert_payload)
    registry = ToolRegistry(_default_connectors(), mcp_servers=mcp_servers or [], mcp_tools=mcp_tools or [])
    tools = registry.list_tools(
        stage_id=WorkflowStageId.RESOLVE_SERVICE_IDENTITY,
        allowlist=prompt_profile.tool_allowlist,
        light_probe_only=True,
    )

    traces: list[AgentToolTrace] = []
    context_results: dict[str, dict[str, Any]] = {}
    seed_identity = {"canonical_service_id": "unknown", "env": "unknown"}

    selected_tools = [tool for tool in tools if tool.name in {"context.alert_entities", f"connector.{alert.source}.discover_context"}]
    if not selected_tools:
        selected_tools = tools[: min(prompt_profile.max_tool_calls, 3)]

    for tool in selected_tools[: prompt_profile.max_tool_calls]:
        result, trace = _execute_tool(registry, tool, alert_payload=alert_payload, service_identity=seed_identity)
        traces.append(trace)
        context_results[tool.name] = result

    entities = list(dict.fromkeys(alert.entity_ids))
    nr_candidates = entities if alert.source == "newrelic" else []
    azure_candidates = entities
    if connector_ctx := context_results.get(f"connector.{alert.source}.discover_context", {}):
        discovered_ids = connector_ctx.get("entity_ids") or connector_ctx.get("resource_ids") or connector_ctx.get("trace_ids") or []
        if isinstance(discovered_ids, list):
            entities = list(dict.fromkeys([*entities, *[str(item) for item in discovered_ids]]))
    cmdb_candidates = [f"{entity}-cmdb" for entity in entities[:1]]
    rag_candidates = [f"{entity}-rag" for entity in entities[:1]]

    identity = resolve_service_identity(
        alert=alert,
        nr_candidates=nr_candidates,
        azure_candidates=azure_candidates,
        cmdb_candidates=cmdb_candidates,
        rag_candidates=rag_candidates,
    )

    objective = _resolver_objective(prompt_profile, alert)
    model_used, llm_summary = _model_route_summary(model_route, objective)
    reasoning = (
        f"Model {model_used} selected candidates from alert entities and light context probes; "
        f"chose {identity.canonical_service_id} with confidence {identity.confidence:.2f}."
    )
    payload = identity.model_dump(mode="json")
    return AgentExecutionResult(
        payload=payload,
        llm_model_used=model_used,
        llm_summary=llm_summary,
        stage_reasoning_summary=reasoning,
        tool_traces=traces,
    )


def run_planner_agent(
    *,
    investigation_id: str,
    alert_payload: dict[str, Any],
    model_route: ModelRoute,
    prompt_profile: AgentPromptProfile,
    mcp_servers: list[Any] | None = None,
    mcp_tools: list[Any] | None = None,
) -> AgentExecutionResult:
    alert = AlertEnvelope.model_validate(alert_payload)
    registry = ToolRegistry(_default_connectors(), mcp_servers=mcp_servers or [], mcp_tools=mcp_tools or [])
    tools = registry.list_tools(
        stage_id=WorkflowStageId.BUILD_INVESTIGATION_PLAN,
        allowlist=prompt_profile.tool_allowlist,
        light_probe_only=True,
    )

    traces: list[AgentToolTrace] = []
    selected_tools = [tool for tool in tools if tool.name.startswith("connector.") and tool.light_probe]
    selected_tools = selected_tools[: max(1, min(prompt_profile.max_tool_calls, 4))]
    if inventory_tool := next((tool for tool in tools if tool.name == "context.tool_inventory"), None):
        selected_tools = [inventory_tool, *selected_tools]

    for tool in selected_tools[: prompt_profile.max_tool_calls]:
        _, trace = _execute_tool(registry, tool, alert_payload=alert_payload, service_identity={})
        traces.append(trace)

    plan = build_default_plan(investigation_id=investigation_id, alert=alert)
    # Keep planning bounded; adjust first provider to alert source when possible.
    if plan.ordered_steps:
        preferred_provider = "newrelic" if alert.source == "newrelic" else "otel"
        plan.ordered_steps[0] = PlanStep(
            provider=preferred_provider,
            rationale=plan.ordered_steps[0].rationale,
            timeout_seconds=plan.ordered_steps[0].timeout_seconds,
            budget_weight=plan.ordered_steps[0].budget_weight,
            capability=plan.ordered_steps[0].capability,
        )
    enforce_budget_policy(plan)

    # Ensure final payload validates after model reasoning path.
    validated_plan = InvestigationPlan.model_validate(plan.model_dump(mode="json"))
    objective = _planner_objective(prompt_profile, alert)
    model_used, llm_summary = _model_route_summary(model_route, objective)
    reasoning = (
        f"Model {model_used} selected a bounded {len(validated_plan.ordered_steps)}-step plan "
        "using only light probes and policy budgets."
    )
    return AgentExecutionResult(
        payload=validated_plan.model_dump(mode="json"),
        llm_model_used=model_used,
        llm_summary=llm_summary,
        stage_reasoning_summary=reasoning,
        tool_traces=traces,
    )
