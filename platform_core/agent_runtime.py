from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any

from platform_core.llm_router import ModelRoute, resolve_model_alias, summarize_with_model_route
from platform_core.mcp_client import invoke_mcp_tool
from platform_core.mcp_execution import (
    blocked_tool_entries,
    extract_artifact_update,
    invocable_tool_names,
    merge_artifact_state,
    resolve_service_aliases,
    seed_artifact_state,
)
from platform_core.mcp_planning import build_mcp_only_plan, derive_argument_context, select_mcp_tools
from platform_core.models import (
    AgentPromptProfile,
    AgentToolTrace,
    AlertEnvelope,
    ArtifactState,
    InvestigationPlan,
    McpServerConfig,
    McpToolDescriptor,
    ServiceIdentity,
)
from platform_core.policy import enforce_budget_policy


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
    artifact_state: dict[str, Any] | None = None
    resolved_aliases: list[dict[str, Any]] | None = None
    blocked_tools: list[dict[str, Any]] | None = None
    invocable_tools: list[str] | None = None


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


def _parse_time_like(value: Any, *, now: datetime | None = None) -> datetime | None:
    if now is None:
        now = datetime.now(timezone.utc)

    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1_000_000_000_000:
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None

    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    lowered = text.lower().replace(" ", "")
    if lowered == "now":
        return now

    relative = re.fullmatch(r"now([+-])(\d+)([smhd])", lowered)
    if relative:
        sign, amount_text, unit = relative.groups()
        amount = int(amount_text)
        if unit == "s":
            delta = timedelta(seconds=amount)
        elif unit == "m":
            delta = timedelta(minutes=amount)
        elif unit == "h":
            delta = timedelta(hours=amount)
        else:
            delta = timedelta(days=amount)
        return now - delta if sign == "-" else now + delta

    if lowered.isdigit():
        return _parse_time_like(int(lowered), now=now)

    iso_candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_tool_arguments(descriptor: McpToolDescriptor, arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(arguments or {})
    server = descriptor.server_id.lower()
    tool = descriptor.tool_name.lower()
    now = datetime.now(timezone.utc)

    # Grafana annotations API expects epoch milliseconds for From/To.
    if server == "grafana" and tool == "get_annotations":
        for key in list(normalized.keys()):
            if key.lower() not in {"from", "to"}:
                continue
            parsed = _parse_time_like(normalized[key], now=now)
            if parsed:
                normalized[key] = int(parsed.timestamp() * 1000)

    # Some Grafana MCP analytical tools expect absolute RFC3339 start/end timestamps.
    if server == "grafana" and tool in {"find_slow_requests", "find_error_logs", "find_slow_db_queries"}:
        for key in list(normalized.keys()):
            if key.lower() not in {"start", "end"}:
                continue
            parsed = _parse_time_like(normalized[key], now=now)
            if parsed:
                normalized[key] = parsed.isoformat().replace("+00:00", "Z")

    # Jaeger search API rejects plain service-name strings in `tags`.
    # Keep only structured/kv forms; otherwise omit optional tags.
    if server == "jaeger":
        for key in list(normalized.keys()):
            if key.lower() != "tags":
                continue
            value = normalized[key]
            if isinstance(value, dict):
                continue
            if isinstance(value, str):
                text = value.strip()
                if text and ("=" in text or (text.startswith("{") and text.endswith("}"))):
                    normalized[key] = text
                    continue
                normalized.pop(key, None)
                continue
            if isinstance(value, list):
                pairs = [item.strip() for item in value if isinstance(item, str) and "=" in item]
                if pairs:
                    normalized[key] = ",".join(pairs)
                else:
                    normalized.pop(key, None)
                continue
            normalized.pop(key, None)

    return normalized


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
    normalized_arguments = _normalize_tool_arguments(descriptor, arguments)

    try:
        server = servers.get(descriptor.server_id)
        if not server:
            raise ValueError(f"MCP server unavailable: {descriptor.server_id}")
        result = invoke_mcp_tool(server, descriptor.tool_name, normalized_arguments)
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
        args_summary=_sanitize_value(normalized_arguments),
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
        "Goal: produce a bounded MCP-only investigation plan that helps fix the alert manually.\n"
        "If the current service scope or evidence preconditions are insufficient, append optional rerun fields:\n"
        "RERUN_STAGE: <resolve_service_identity|build_investigation_plan>\n"
        "RERUN_REASON: <why current scope/plan is insufficient>\n"
        "RERUN_OBJECTIVE: <what the rerun should discover>\n"
        "RERUN_EVIDENCE: <missing artifact or evidence class>\n"
        "RERUN_TOOL_FOCUS: <comma-separated tool names>"
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
    artifact_state = seed_artifact_state(alert_payload, {})
    context = derive_argument_context(alert_payload, {}, artifact_state)
    servers = _server_map(mcp_servers)
    requested_model = {"primary": model_route.primary, "fallback": model_route.fallback}

    selected_tools, skipped_tools = select_mcp_tools(
        mcp_tools or [],
        context,
        allowlist=prompt_profile.tool_allowlist,
        max_tools=max(1, min(prompt_profile.max_tool_calls, 4)),
        mode="discovery",
        light_probe_only=True,
        artifact_state=artifact_state,
        alert_payload=alert_payload,
    )

    traces: list[AgentToolTrace] = []
    for planned_tool in selected_tools:
        result, trace = _execute_mcp_tool(servers, planned_tool.descriptor, planned_tool.arguments)
        traces.append(trace)
        artifact_state = merge_artifact_state(artifact_state, extract_artifact_update(planned_tool.descriptor, result))

    artifact_state, resolved_aliases = resolve_service_aliases(artifact_state)

    candidate_pool = list(dict.fromkeys([*artifact_state.service_candidates, *artifact_state.metric_service_candidates]))
    alias_trace = artifact_state.alias_decision_trace
    resolved_service = str(artifact_state.resolved_service or "").strip()
    has_alias_resolution = bool(alias_trace and alias_trace.selected_candidate and resolved_service)
    canonical_service_id = resolved_service if has_alias_resolution else "unknown"
    ambiguous = candidate_pool[:3] if not has_alias_resolution else [candidate for candidate in candidate_pool if candidate != canonical_service_id][:3]
    alias_confidence = alias_trace.confidence if alias_trace and has_alias_resolution else 0.0
    identity = ServiceIdentity(
        canonical_service_id=canonical_service_id,
        owner=alert.raw_payload.get("owner"),
        env=str(alert.raw_payload.get("env", "prod")),
        dependency_graph_refs=[str(item) for item in alert.raw_payload.get("deps", []) if isinstance(item, str)],
        mapped_provider_ids=(
            {
                "primary": canonical_service_id,
                **({"jaeger": canonical_service_id} if any(tool.server_id == "jaeger" for tool in (mcp_tools or [])) else {}),
                **({"prometheus": canonical_service_id} if any(tool.server_id == "prometheus" for tool in (mcp_tools or [])) else {}),
            }
            if has_alias_resolution
            else {}
        ),
        confidence=max(0.0, min(alias_confidence, 0.99)),
        ambiguous_candidates=ambiguous,
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
    if has_alias_resolution:
        reasoning = (
            f"Model {model_used} resolved service identity from alert context using {len(traces)} MCP probe(s); "
            f"skipped {len(skipped_tools)} tool(s) with unmet requirements; "
            f"selected {identity.canonical_service_id} with confidence {identity.confidence:.2f} "
            f"after discovering {len(candidate_pool)} telemetry service candidate(s)."
        )
    else:
        unresolved_reason = alias_trace.unresolved_reason if alias_trace else "alias_resolution_missing"
        reasoning = (
            f"Model {model_used} could not resolve a canonical service identity after {len(traces)} MCP probe(s); "
            f"skipped {len(skipped_tools)} tool(s) with unmet requirements; "
            f"discovered {len(candidate_pool)} telemetry service candidate(s); unresolved_reason={unresolved_reason}."
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
        artifact_state=artifact_state.model_dump(mode="json"),
        resolved_aliases=[item.model_dump(mode="json") for item in resolved_aliases],
        blocked_tools=blocked_tool_entries(mcp_tools or [], artifact_state),
        invocable_tools=invocable_tool_names(mcp_tools or [], artifact_state),
    )


def run_planner_agent(
    *,
    investigation_id: str,
    alert_payload: dict[str, Any],
    model_route: ModelRoute,
    prompt_profile: AgentPromptProfile,
    mcp_servers: list[McpServerConfig] | None = None,
    mcp_tools: list[McpToolDescriptor] | None = None,
    service_identity: dict[str, Any] | None = None,
    artifact_state_payload: dict[str, Any] | None = None,
) -> AgentExecutionResult:
    alert = AlertEnvelope.model_validate(alert_payload)
    artifact_state = (
        ArtifactState.model_validate(artifact_state_payload)
        if isinstance(artifact_state_payload, dict)
        else seed_artifact_state(alert_payload, service_identity or {})
    )
    artifact_state, resolved_aliases = resolve_service_aliases(artifact_state)
    context = derive_argument_context(alert_payload, service_identity or {}, artifact_state)
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
        artifact_state=artifact_state,
        alert_payload=alert_payload,
    )

    traces: list[AgentToolTrace] = []
    for planned_tool in probe_tools:
        result, trace = _execute_mcp_tool(servers, planned_tool.descriptor, planned_tool.arguments)
        traces.append(trace)
        artifact_state = merge_artifact_state(artifact_state, extract_artifact_update(planned_tool.descriptor, result))

    artifact_state, resolved_aliases = resolve_service_aliases(artifact_state)
    context = derive_argument_context(alert_payload, service_identity or {}, artifact_state)

    plan, plan_skipped = build_mcp_only_plan(
        investigation_id=investigation_id,
        tools=tools,
        context=context,
        allowlist=prompt_profile.tool_allowlist,
        max_steps=max(1, min(prompt_profile.max_tool_calls, 6)),
        max_api_calls=10,
        max_stage_wall_clock_seconds=600,
        artifact_state=artifact_state,
        alert_payload=alert_payload,
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
        f"{len(traces)} light probe(s); skipped {len(skipped_tools)} tool(s); "
        f"resolved_service={artifact_state.resolved_service or 'unknown'}."
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
        artifact_state=artifact_state.model_dump(mode="json"),
        resolved_aliases=[item.model_dump(mode="json") for item in resolved_aliases],
        blocked_tools=blocked_tool_entries(tools, artifact_state),
        invocable_tools=invocable_tool_names(tools, artifact_state),
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
