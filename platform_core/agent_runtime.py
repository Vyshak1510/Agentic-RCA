from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from platform_core.llm_router import ModelRoute, resolve_model_alias, summarize_with_model_route
from platform_core.mcp_client import invoke_mcp_tool
from platform_core.mcp_planning import build_mcp_only_plan, derive_argument_context, select_mcp_tools
from platform_core.models import (
    AgentPromptProfile,
    AgentToolTrace,
    AlertEnvelope,
    InvestigationPlan,
    McpServerConfig,
    McpToolDescriptor,
    ServiceIdentity,
)
from platform_core.policy import enforce_budget_policy
from platform_core.resolver import resolve_service_identity


@dataclass
class AgentExecutionResult:
    payload: dict[str, Any]
    llm_model_used: str
    llm_summary: str
    stage_reasoning_summary: str
    tool_traces: list[AgentToolTrace]
    skipped_tools: list[dict[str, Any]]
    requested_model: dict[str, str]
    resolved_model: dict[str, str]
    model_error: str | None = None


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


def _dedupe_skipped_tools(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in entries:
        key = str(
            (
                item.get("tool_name"),
                item.get("reason"),
                tuple(item.get("missing_required_args", [])),
                item.get("error"),
            )
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _server_map(mcp_servers: list[McpServerConfig] | None) -> dict[str, McpServerConfig]:
    servers: dict[str, McpServerConfig] = {}
    for server in mcp_servers or []:
        if server.enabled:
            servers[server.server_id] = server
    return servers


def _model_route_summary(route: ModelRoute, objective: str) -> tuple[str, str, dict[str, str]]:
    resolved_primary = resolve_model_alias(route.primary)
    resolved_fallback = resolve_model_alias(route.fallback)
    resolved = {"primary": resolved_primary, "fallback": resolved_fallback}

    effective_route = ModelRoute(primary=resolved_primary, fallback=resolved_fallback, key_ref=route.key_ref)
    if os.getenv("SIMULATE_PRIMARY_LLM_FAILURE") == "1":
        effective_route = ModelRoute(primary="invalid-model", fallback=resolved_fallback, key_ref=route.key_ref)

    model_used, llm_summary = summarize_with_model_route(
        effective_route,
        objective,
        system_prompt="You are an RCA agent stage assistant. Keep the output concise and actionable.",
        max_tokens=220,
    )
    return model_used, llm_summary, resolved


def _execute_mcp_tool(
    servers: dict[str, McpServerConfig],
    descriptor: McpToolDescriptor,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], AgentToolTrace]:
    started = datetime.now(timezone.utc)
    timer = perf_counter()
    fqdn = f"mcp.{descriptor.server_id}.{descriptor.tool_name}"
    error: str | None = None

    try:
        server = servers.get(descriptor.server_id)
        if not server:
            raise ValueError(f"MCP server unavailable: {descriptor.server_id}")
        result = invoke_mcp_tool(server, descriptor.tool_name, arguments)
        success = True
    except Exception as exc:
        success = False
        error = str(exc)
        result = {"error": str(exc)}

    ended = datetime.now(timezone.utc)
    trace = AgentToolTrace(
        tool_name=fqdn,
        source="mcp",
        read_only=True,
        started_at=started,
        ended_at=ended,
        duration_ms=int((perf_counter() - timer) * 1000),
        success=success,
        args_summary=_sanitize_value(arguments),
        result_summary=_sanitize_value(result),
        error=error,
        citations=[],
    )
    return result, trace


def _resolver_objective(profile: AgentPromptProfile, alert: AlertEnvelope) -> str:
    objective = profile.objective_template.replace("{{incident_key}}", alert.incident_key)
    return (
        f"{profile.system_prompt}\n"
        f"Objective: {objective}\n"
        "Constraints: read-only MCP tools only, output canonical service identity and ambiguity.\n"
        "Goal: resolve the alert root-cause investigation context for manual remediation."
    )


def _planner_objective(profile: AgentPromptProfile, alert: AlertEnvelope) -> str:
    objective = profile.objective_template.replace("{{incident_key}}", alert.incident_key)
    return (
        f"{profile.system_prompt}\n"
        f"Objective: {objective}\n"
        "Constraints: planning stage allows MCP light probes only; do not fetch deep evidence.\n"
        "Goal: produce a bounded MCP-only investigation plan that helps fix the alert manually."
    )


def run_resolver_agent(
    *,
    alert_payload: dict[str, Any],
    model_route: ModelRoute,
    prompt_profile: AgentPromptProfile,
    mcp_servers: list[McpServerConfig] | None = None,
    mcp_tools: list[McpToolDescriptor] | None = None,
) -> AgentExecutionResult:
    alert = AlertEnvelope.model_validate(alert_payload)
    context = derive_argument_context(alert_payload, {})
    servers = _server_map(mcp_servers)
    requested_model = {"primary": model_route.primary, "fallback": model_route.fallback}

    selected_tools, skipped_tools = select_mcp_tools(
        mcp_tools or [],
        context,
        allowlist=prompt_profile.tool_allowlist,
        max_tools=max(1, min(prompt_profile.max_tool_calls, 4)),
        mode="discovery",
        light_probe_only=True,
    )

    traces: list[AgentToolTrace] = []
    for planned_tool in selected_tools:
        _, trace = _execute_mcp_tool(servers, planned_tool.descriptor, planned_tool.arguments)
        traces.append(trace)

    entities = list(dict.fromkeys(alert.entity_ids))
    if entities:
        nr_candidates = entities
    else:
        nr_candidates = ["unknown-service"]

    identity = resolve_service_identity(
        alert=alert,
        nr_candidates=nr_candidates,
        azure_candidates=[],
        cmdb_candidates=[],
        rag_candidates=[],
    )

    objective = _resolver_objective(prompt_profile, alert)
    model_error: str | None = None
    try:
        model_used, llm_summary, resolved_model = _model_route_summary(model_route, objective)
    except Exception as exc:
        model_used = "unavailable"
        llm_summary = f"model_error: {exc}"
        model_error = str(exc)
        resolved_model = {}
    reasoning = (
        f"Model {model_used} resolved service identity from alert context using {len(traces)} MCP probe(s); "
        f"skipped {len(skipped_tools)} tool(s) with unmet requirements; "
        f"selected {identity.canonical_service_id} with confidence {identity.confidence:.2f}."
    )

    return AgentExecutionResult(
        payload=ServiceIdentity.model_validate(identity).model_dump(mode="json"),
        llm_model_used=model_used,
        llm_summary=llm_summary,
        stage_reasoning_summary=reasoning,
        tool_traces=traces,
        skipped_tools=skipped_tools,
        requested_model=requested_model,
        resolved_model=resolved_model,
        model_error=model_error,
    )


def run_planner_agent(
    *,
    investigation_id: str,
    alert_payload: dict[str, Any],
    model_route: ModelRoute,
    prompt_profile: AgentPromptProfile,
    mcp_servers: list[McpServerConfig] | None = None,
    mcp_tools: list[McpToolDescriptor] | None = None,
) -> AgentExecutionResult:
    alert = AlertEnvelope.model_validate(alert_payload)
    context = derive_argument_context(alert_payload, {})
    servers = _server_map(mcp_servers)
    tools = mcp_tools or []
    requested_model = {"primary": model_route.primary, "fallback": model_route.fallback}

    probe_tools, probe_skipped = select_mcp_tools(
        tools,
        context,
        allowlist=prompt_profile.tool_allowlist,
        max_tools=max(1, min(prompt_profile.max_tool_calls, 4)),
        mode="discovery",
        light_probe_only=True,
    )

    traces: list[AgentToolTrace] = []
    for planned_tool in probe_tools:
        _, trace = _execute_mcp_tool(servers, planned_tool.descriptor, planned_tool.arguments)
        traces.append(trace)

    plan, plan_skipped = build_mcp_only_plan(
        investigation_id=investigation_id,
        tools=tools,
        context=context,
        allowlist=prompt_profile.tool_allowlist,
        max_steps=max(1, min(prompt_profile.max_tool_calls, 6)),
        max_api_calls=10,
        max_stage_wall_clock_seconds=600,
    )
    enforce_budget_policy(plan)
    validated_plan = InvestigationPlan.model_validate(plan.model_dump(mode="json"))

    objective = _planner_objective(prompt_profile, alert)
    model_error: str | None = None
    try:
        model_used, llm_summary, resolved_model = _model_route_summary(model_route, objective)
    except Exception as exc:
        model_used = "unavailable"
        llm_summary = f"model_error: {exc}"
        model_error = str(exc)
        resolved_model = {}

    skipped_tools = _dedupe_skipped_tools(probe_skipped + plan_skipped)
    reasoning = (
        f"Model {model_used} planned {len(validated_plan.ordered_steps)} MCP evidence step(s) after "
        f"{len(traces)} light probe(s); skipped {len(skipped_tools)} tool(s) due to missing required arguments."
    )

    return AgentExecutionResult(
        payload=validated_plan.model_dump(mode="json"),
        llm_model_used=model_used,
        llm_summary=llm_summary,
        stage_reasoning_summary=reasoning,
        tool_traces=traces,
        skipped_tools=skipped_tools,
        requested_model=requested_model,
        resolved_model=resolved_model,
        model_error=model_error,
    )


def run_evidence_agent(
    *,
    investigation_id: str,
    alert_payload: dict[str, Any],
    model_route: ModelRoute,
    prompt_profile: AgentPromptProfile,
    mcp_servers: list[McpServerConfig] | None = None,
    mcp_tools: list[McpToolDescriptor] | None = None,
    max_iterations: int = 8,
) -> AgentExecutionResult:
    """LLM-driven ReAct loop for evidence collection.

    The model sees tool results and decides what to call next, enabling
    drill-down chains (e.g. search_traces → get_trace on the errored trace).
    """
    from litellm import completion as litellm_completion
    from platform_core.llm_router import _api_base, _is_reasoning_model

    alert = AlertEnvelope.model_validate(alert_payload)
    servers = _server_map(mcp_servers)
    requested_model = {"primary": model_route.primary, "fallback": model_route.fallback}
    resolved_primary = resolve_model_alias(model_route.primary)
    resolved_fallback = resolve_model_alias(model_route.fallback)
    resolved_model_info = {"primary": resolved_primary, "fallback": resolved_fallback}

    # Only offer read-only tools to the agent
    available_tools = [t for t in (mcp_tools or []) if t.read_only]

    # Build LiteLLM-compatible tool schemas; fn_name encodes server + tool
    litellm_tools: list[dict[str, Any]] = []
    tool_map: dict[str, McpToolDescriptor] = {}
    for descriptor in available_tools:
        fn_name = f"{descriptor.server_id}__{descriptor.tool_name}"
        properties = {key: {"type": "string"} for key in descriptor.arg_keys}
        litellm_tools.append(
            {
                "type": "function",
                "function": {
                    "name": fn_name,
                    "description": descriptor.description or descriptor.tool_name,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": descriptor.required_args,
                    },
                },
            }
        )
        tool_map[fn_name] = descriptor

    entity_ids = ", ".join(alert.entity_ids) if alert.entity_ids else "unknown"
    system_content = (
        "You are an SRE incident investigator with access to observability tools.\n"
        f"Alert: {alert.incident_key} — {json.dumps(alert.raw_payload)}\n"
        f"Service under investigation: {entity_ids}\n"
        "Your job: gather evidence to identify the root cause using observability tools.\n"
        "IMPORTANT: You MUST call at least one tool before drawing any conclusions. "
        "Never infer root cause from the alert name or description alone — always verify with real data.\n"
        "Start broad (list services, search traces with error filter), then drill in (get_trace on errored traces).\n"
        "Stop calling tools only after you have reviewed actual span/metric data."
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"Investigate incident {alert.incident_key}. Use tools to find the root cause."},
    ]

    traces: list[AgentToolTrace] = []
    evidence_payloads: list[dict[str, Any]] = []
    model_used = resolved_primary
    model_error: str | None = None
    conclusion = ""
    active_model = resolved_primary
    iterations_run = 0

    def _do_completion(model: str, first_turn: bool = False) -> Any:
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": 1024}
        if litellm_tools:
            kwargs["tools"] = litellm_tools
            kwargs["tool_choice"] = "required" if first_turn else "auto"
        if not _is_reasoning_model(model):
            kwargs["temperature"] = float(os.getenv("LLM_TEMPERATURE", "0.1"))
        api_key = (os.getenv(model_route.key_ref) if model_route.key_ref else None) or os.getenv("LITELLM_API_KEY")
        if api_key:
            kwargs["api_key"] = api_key
        api_base = _api_base()
        if api_base:
            kwargs["api_base"] = api_base
        return litellm_completion(**kwargs)

    for iteration in range(max_iterations):
        iterations_run = iteration + 1
        first_turn = iteration == 0
        try:
            response = _do_completion(active_model, first_turn=first_turn)
        except Exception as exc:
            if active_model == resolved_primary:
                active_model = resolved_fallback
                try:
                    response = _do_completion(active_model, first_turn=first_turn)
                except Exception as exc2:
                    model_error = str(exc2)
                    break
            else:
                model_error = str(exc)
                break

        model_used = active_model
        choice = response.choices[0]
        message = choice.message
        tool_calls = getattr(message, "tool_calls", None)
        content = getattr(message, "content", None) or ""

        # Serialize assistant message back into history
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ]
        messages.append(assistant_msg)

        if not tool_calls:
            conclusion = content
            break

        # Execute each tool call and feed results back
        for tc in tool_calls:
            fn_name = tc.function.name
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except Exception:
                arguments = {}

            descriptor = tool_map.get(fn_name)
            if not descriptor:
                tool_result_str = json.dumps({"error": f"Unknown tool: {fn_name}"})
            else:
                tool_result, trace = _execute_mcp_tool(servers, descriptor, arguments)
                traces.append(trace)
                evidence_payloads.append(
                    {
                        "tool": fn_name,
                        "server_id": descriptor.server_id,
                        "tool_name": descriptor.tool_name,
                        "arguments": arguments,
                        "result": tool_result,
                    }
                )
                tool_result_str = json.dumps(tool_result)

            messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result_str})

    reasoning = (
        f"Evidence agent (model={model_used}) executed {len(traces)} tool call(s) "
        f"across {iterations_run} iteration(s); collected {len(evidence_payloads)} evidence item(s)."
    )
    return AgentExecutionResult(
        payload={"evidence_payloads": evidence_payloads, "conclusion": conclusion},
        llm_model_used=model_used,
        llm_summary=conclusion or f"Agent collected {len(evidence_payloads)} evidence item(s).",
        stage_reasoning_summary=reasoning,
        tool_traces=traces,
        skipped_tools=[],
        requested_model=requested_model,
        resolved_model=resolved_model_info,
        model_error=model_error,
    )
