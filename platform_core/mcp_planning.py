from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from typing import Any

from platform_core.mcp_execution import (
    bind_artifact_arguments,
    resolve_service_aliases,
    seed_artifact_state,
    tool_diagnostics,
)
from platform_core.models import (
    AlertEnvelope,
    ArtifactState,
    InvestigationPlan,
    McpExecutionPhase,
    McpScopeKind,
    McpToolDescriptor,
    PlanStep,
)

_ARG_ALIAS_CANDIDATES: dict[str, list[str]] = {
    "service": ["service", "service_name", "serviceName", "canonical_service_id", "entity_id", "entity"],
    "service_name": ["service", "service_name", "serviceName", "canonical_service_id", "entity_id", "entity"],
    "serviceName": ["service", "service_name", "serviceName", "canonical_service_id", "entity_id", "entity"],
    "tags": ["tags", "tag", "service", "service_name", "serviceName", "canonical_service_id", "entity_id", "entity"],
    "tag": ["tag", "tags", "service", "service_name", "serviceName", "canonical_service_id", "entity_id", "entity"],
    "uid": ["uid", "alert_uid", "alertRuleUid", "alert_rule_uid"],
    "alertgroupid": ["alertGroupId", "alert_group_id", "group_id", "groupKey", "group_key"],
    "alertGroupId": ["alertGroupId", "alert_group_id", "group_id", "groupKey", "group_key"],
    "query": ["query", "title", "summary", "description", "incident_key"],
    "from": ["from", "start", "started_at", "triggered_at"],
    "to": ["to", "end", "updated_at", "resolved_at"],
}


@dataclass(frozen=True)
class PlannedMcpTool:
    descriptor: McpToolDescriptor
    arguments: dict[str, Any]
    artifact_state: ArtifactState | None = None


def _norm(value: str) -> str:
    return value.strip().lower().replace("-", "").replace("_", "")


def _allowlist_matches(tool_name: str, allowlist: list[str] | None) -> bool:
    if not allowlist:
        return True
    for entry in allowlist:
        token = entry.strip()
        if not token:
            continue
        if token.endswith("*"):
            if tool_name.startswith(token[:-1]):
                return True
            continue
        if token == tool_name:
            return True
    return False


def filter_tools_by_allowlist(tools: list[McpToolDescriptor], allowlist: list[str] | None) -> list[McpToolDescriptor]:
    filtered: list[McpToolDescriptor] = []
    for tool in tools:
        fqdn = f"mcp.{tool.server_id}.{tool.tool_name}"
        if _allowlist_matches(fqdn, allowlist):
            filtered.append(tool)
    return filtered


def _extract_scalar_values(payload: Any, *, depth: int = 0, max_depth: int = 5) -> dict[str, Any]:
    if depth > max_depth:
        return {}

    values: dict[str, Any] = {}
    if isinstance(payload, dict):
        for raw_key, raw_value in payload.items():
            key = str(raw_key)
            if not key:
                continue
            if isinstance(raw_value, (str, int, float, bool)) and raw_value is not None:
                values.setdefault(key, raw_value)
                values.setdefault(_norm(key), raw_value)
                continue
            nested = _extract_scalar_values(raw_value, depth=depth + 1, max_depth=max_depth)
            for nested_key, nested_value in nested.items():
                values.setdefault(nested_key, nested_value)
    elif isinstance(payload, list):
        for item in payload:
            nested = _extract_scalar_values(item, depth=depth + 1, max_depth=max_depth)
            for nested_key, nested_value in nested.items():
                values.setdefault(nested_key, nested_value)

    return values


def derive_argument_context(
    alert_payload: dict[str, Any],
    service_identity: dict[str, Any] | None = None,
    artifact_state: ArtifactState | None = None,
) -> dict[str, Any]:
    alert = AlertEnvelope.model_validate(alert_payload)
    context: dict[str, Any] = {
        "source": alert.source,
        "severity": alert.severity,
        "incident_key": alert.incident_key,
        "entity_ids": alert.entity_ids,
    }

    if alert.entity_ids:
        context["entity_id"] = alert.entity_ids[0]
        context["entity"] = alert.entity_ids[0]

    canonical_service_id = ""
    if isinstance(service_identity, dict):
        canonical_service_id = str(service_identity.get("canonical_service_id") or "").strip()

    resolved_service = artifact_state.resolved_service if artifact_state else None
    if resolved_service:
        canonical_service_id = resolved_service

    if canonical_service_id:
        context["service"] = canonical_service_id
        context["service_name"] = canonical_service_id
        context["serviceName"] = canonical_service_id
        context["canonical_service_id"] = canonical_service_id

    for key, value in (alert.timestamps or {}).items():
        if value is None:
            continue
        if hasattr(value, "isoformat"):
            context[key] = value.isoformat()
        else:
            context[key] = str(value)

    if isinstance(alert.raw_payload, dict):
        flattened = _extract_scalar_values(alert.raw_payload)
        for key, value in flattened.items():
            if key not in context:
                context[key] = value

    normalized_index = {_norm(key): value for key, value in context.items()}
    for key, value in normalized_index.items():
        context.setdefault(key, value)

    return context


def _artifact_state_from_context(context: dict[str, Any]) -> ArtifactState:
    state = ArtifactState(
        alert_terms=[str(value) for value in context.get("entity_ids", []) if isinstance(value, str)],
        resolved_service=(str(context.get("canonical_service_id") or "").strip() or None),
    )
    if state.resolved_service:
        state.service_candidates = [state.resolved_service]
    return state


def derive_tool_arguments(tool: McpToolDescriptor, context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    server = tool.server_id.strip().lower()

    def maybe_coerce(arg: str, value: Any) -> Any:
        normalized = _norm(arg)

        if normalized == "tag":
            if isinstance(value, str):
                text = value.strip()
                return text or None
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        return item.strip()
                return None
            return value

        if normalized == "tags":
            # Jaeger tags are not plain service-name strings; passing those
            # triggers 400s. Keep only structurally valid tag payloads.
            if server == "jaeger":
                if isinstance(value, dict):
                    sanitized: dict[str, str] = {}
                    for key, item in value.items():
                        key_text = str(key).strip()
                        if not key_text:
                            continue
                        if isinstance(item, (str, int, float, bool)):
                            item_text = str(item).strip()
                            if item_text:
                                sanitized[key_text] = item_text
                    return sanitized or None
                if isinstance(value, str):
                    text = value.strip()
                    if not text:
                        return None
                    if text.startswith("{") and text.endswith("}"):
                        return text
                    if "=" in text:
                        return text
                    return None
                if isinstance(value, list):
                    pairs = [item.strip() for item in value if isinstance(item, str) and "=" in item]
                    if not pairs:
                        return None
                    return ",".join(pairs)
                return None

            if isinstance(value, str):
                return [value]
            if isinstance(value, list):
                return [item for item in value if isinstance(item, str) and item]
            return value

        if normalized not in {"from", "to"}:
            return value

        # Grafana annotation APIs expect integer timestamps (epoch millis).
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return value
            if text.isdigit():
                return int(text)
            iso_candidate = text.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(iso_candidate)
            except ValueError:
                return value
            return int(dt.timestamp() * 1000)
        return value

    def lookup(arg: str) -> Any:
        candidates = _ARG_ALIAS_CANDIDATES.get(arg, [])
        normalized = _norm(arg)
        candidates.extend(_ARG_ALIAS_CANDIDATES.get(normalized, []))
        candidates.extend([arg, normalized])
        for candidate in candidates:
            if candidate in context and context[candidate] not in (None, ""):
                coerced = maybe_coerce(arg, context[candidate])
                if coerced is None:
                    continue
                return coerced
            canonical = _norm(candidate)
            if canonical in context and context[canonical] not in (None, ""):
                coerced = maybe_coerce(arg, context[canonical])
                if coerced is None:
                    continue
                return coerced
        return None

    args: dict[str, Any] = {}
    missing: list[str] = []

    for required in tool.required_args:
        value = lookup(required)
        if value is None:
            missing.append(required)
            continue
        args[required] = value

    for key in tool.arg_keys:
        if key in args:
            continue
        value = lookup(key)
        if value is None:
            continue
        args[key] = value

    return args, missing


def _tool_priority(
    tool: McpToolDescriptor,
    mode: str,
    artifact_state: ArtifactState | None = None,
) -> tuple[int, int, int, int, str, str]:
    phase_weight = {
        McpExecutionPhase.DISCOVER: 0,
        McpExecutionPhase.RESOLVE: 1,
        McpExecutionPhase.INSPECT: 2,
        McpExecutionPhase.DRILLDOWN: 3,
    }.get(tool.phase, 9)
    if mode == "discovery":
        phase_weight = 0 if tool.phase in {McpExecutionPhase.DISCOVER, McpExecutionPhase.RESOLVE} else 9
    elif (
        mode == "evidence"
        and artifact_state is not None
        and artifact_state.resolved_service
        and tool.phase == McpExecutionPhase.INSPECT
        and tool.scope_kind in {McpScopeKind.SERVICE, McpScopeKind.METRIC}
    ):
        # Once service identity is resolved, prioritize service-scoped inspect
        # tools so the plan always includes actionable evidence steps.
        phase_weight = -1
    # Prefer lower declared priority and fewer required args.
    return (
        phase_weight,
        int(tool.default_priority),
        len(tool.required_args),
        0 if tool.light_probe else 1,
        tool.server_id,
        tool.tool_name,
    )


def _tool_disabled_by_policy(tool: McpToolDescriptor) -> bool:
    # Grafana OnCall/alert-group tools are environment-dependent and frequently
    # fail in local setups without the OnCall service configured. Keep them off
    # by default for RCA workflows; allow explicit opt-in via env flag.
    if tool.server_id != "grafana":
        return False
    if os.getenv("RCA_GRAFANA_ENABLE_ONCALL_TOOLS", "").strip() == "1":
        return False
    lowered = tool.tool_name.lower()
    return "oncall" in lowered or "alert_group" in lowered


def select_mcp_tools(
    tools: list[McpToolDescriptor],
    context: dict[str, Any],
    *,
    allowlist: list[str] | None,
    max_tools: int,
    mode: str,
    light_probe_only: bool = False,
    artifact_state: ArtifactState | None = None,
    alert_payload: dict[str, Any] | None = None,
) -> tuple[list[PlannedMcpTool], list[dict[str, Any]]]:
    filtered = filter_tools_by_allowlist(tools, allowlist)
    filtered = [tool for tool in filtered if tool.read_only]
    if light_probe_only:
        filtered = [tool for tool in filtered if tool.light_probe]
    if mode == "discovery":
        filtered = [tool for tool in filtered if tool.phase in {McpExecutionPhase.DISCOVER, McpExecutionPhase.RESOLVE}]

    artifact_state = artifact_state or _artifact_state_from_context(context)

    selected: list[PlannedMcpTool] = []
    skipped: list[dict[str, Any]] = []

    for tool in sorted(filtered, key=lambda item: _tool_priority(item, mode, artifact_state)):
        fqdn = f"mcp.{tool.server_id}.{tool.tool_name}"
        if _tool_disabled_by_policy(tool):
            skipped.append(
                {
                    "tool_name": fqdn,
                    "reason": "disabled_by_policy",
                    "detail": "Tool disabled by default RCA policy.",
                }
            )
            continue

        diagnostics = tool_diagnostics(tool, artifact_state)
        if not diagnostics.invocable:
            skipped.append(
                {
                    "tool_name": fqdn,
                    "reason": "missing_artifacts",
                    "missing_artifacts": diagnostics.missing_artifacts,
                }
            )
            continue

        args, missing = derive_tool_arguments(tool, context)
        if alert_payload is not None:
            args = bind_artifact_arguments(tool, args, artifact_state, alert_payload)
        missing = [required for required in tool.required_args if args.get(required) in (None, "")]
        if missing:
            skipped.append(
                {
                    "tool_name": fqdn,
                    "reason": "missing_required_args",
                    "missing_required_args": missing,
                    "available_arg_keys": tool.arg_keys,
                }
            )
            continue

        # Some MCP tools expose optional schema fields but still require
        # at least one identifier in practice (for example get_datasource).
        # Avoid invoking such tools with empty arguments.
        if tool.tool_name.startswith("get_") and tool.arg_keys and not args:
            skipped.append(
                {
                    "tool_name": fqdn,
                    "reason": "missing_context_args",
                    "expected_any_of": tool.arg_keys,
                }
            )
            continue

        selected.append(PlannedMcpTool(descriptor=tool, arguments=args, artifact_state=artifact_state.model_copy(deep=True)))
        if len(selected) >= max_tools:
            break

    return selected, skipped


def build_mcp_only_plan(
    investigation_id: str,
    tools: list[McpToolDescriptor],
    context: dict[str, Any],
    *,
    allowlist: list[str] | None,
    max_steps: int,
    max_api_calls: int,
    max_stage_wall_clock_seconds: int,
    artifact_state: ArtifactState | None = None,
    alert_payload: dict[str, Any] | None = None,
) -> tuple[InvestigationPlan, list[dict[str, Any]]]:
    artifact_state = artifact_state or _artifact_state_from_context(context)
    artifact_state, _ = resolve_service_aliases(artifact_state)
    selected, skipped = select_mcp_tools(
        tools,
        context,
        allowlist=allowlist,
        max_tools=max_steps,
        mode="evidence",
        light_probe_only=False,
        artifact_state=artifact_state,
        alert_payload=alert_payload,
    )
    if not selected:
        selected, discovery_skipped = select_mcp_tools(
            tools,
            context,
            allowlist=allowlist,
            max_tools=max_steps,
            mode="discovery",
            light_probe_only=True,
            artifact_state=artifact_state,
            alert_payload=alert_payload,
        )
        skipped.extend(discovery_skipped)

    steps: list[PlanStep] = []
    budget_used = 0
    for item in selected:
        tool = item.descriptor
        timeout_seconds = 90 if tool.light_probe else 150
        budget_weight = 1 if tool.light_probe else 2
        capability = f"{tool.server_id}.{tool.tool_name}"
        if steps and budget_used + timeout_seconds > max_stage_wall_clock_seconds:
            skipped.append(
                {
                    "tool_name": f"mcp.{tool.server_id}.{tool.tool_name}",
                    "reason": "budget_trimmed",
                    "detail": "Skipped to keep the plan within the stage wall-clock budget.",
                }
            )
            continue

        steps.append(
            PlanStep(
                provider="mcp",
                rationale=tool.description or f"Collect evidence using MCP tool {tool.tool_name}",
                timeout_seconds=timeout_seconds,
                budget_weight=budget_weight,
                capability=capability,
                execution_source="mcp",
                mcp_server_id=tool.server_id,
                mcp_tool_name=tool.tool_name,
                mcp_arguments=item.arguments,
                required_artifacts=tool.requires_artifacts,
                produced_artifacts=tool.produces_artifacts,
            )
        )
        budget_used += timeout_seconds

    plan = InvestigationPlan(
        investigation_id=investigation_id,
        ordered_steps=steps,
        max_api_calls=max(1, max(max_api_calls, len(steps))),
        max_stage_wall_clock_seconds=max(30, max_stage_wall_clock_seconds),
    )
    return plan, skipped
