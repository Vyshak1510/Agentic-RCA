from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from platform_core.models import AlertEnvelope, InvestigationPlan, McpToolDescriptor, PlanStep

_DISCOVERY_PRIORITY: dict[tuple[str, str], int] = {
    ("grafana", "get_annotations"): 0,
    ("grafana", "get_annotation_tags"): 1,
    ("grafana", "search_dashboards"): 2,
    ("grafana", "list_datasources"): 3,
    ("jaeger", "service_operations"): 4,
    ("jaeger", "search_traces"): 5,
    ("jaeger", "find_error_traces"): 6,
}

_EVIDENCE_PRIORITY: dict[tuple[str, str], int] = {
    ("jaeger", "find_error_traces"): 0,
    ("jaeger", "search_traces"): 1,
    ("jaeger", "service_operations"): 2,
    ("grafana", "get_annotations"): 3,
    ("grafana", "get_annotation_tags"): 4,
    ("grafana", "search_dashboards"): 5,
    ("grafana", "list_datasources"): 6,
}

_ARG_ALIAS_CANDIDATES: dict[str, list[str]] = {
    "service": ["service", "service_name", "serviceName", "canonical_service_id", "entity_id", "entity"],
    "service_name": ["service", "service_name", "serviceName", "canonical_service_id", "entity_id", "entity"],
    "serviceName": ["service", "service_name", "serviceName", "canonical_service_id", "entity_id", "entity"],
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
    if not canonical_service_id and alert.entity_ids:
        canonical_service_id = str(alert.entity_ids[0]).strip()

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


def derive_tool_arguments(tool: McpToolDescriptor, context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    def lookup(arg: str) -> Any:
        candidates = _ARG_ALIAS_CANDIDATES.get(arg, [])
        normalized = _norm(arg)
        candidates.extend(_ARG_ALIAS_CANDIDATES.get(normalized, []))
        candidates.extend([arg, normalized])
        for candidate in candidates:
            if candidate in context and context[candidate] not in (None, ""):
                return context[candidate]
            canonical = _norm(candidate)
            if canonical in context and context[canonical] not in (None, ""):
                return context[canonical]
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


def _tool_priority(tool: McpToolDescriptor, mode: str) -> tuple[int, int, int, str, str]:
    if mode == "evidence":
        priority = _EVIDENCE_PRIORITY.get((tool.server_id, tool.tool_name), 100)
    else:
        priority = _DISCOVERY_PRIORITY.get((tool.server_id, tool.tool_name), 100)
    # Prefer tools with fewer required args in discovery for higher invocation reliability.
    return (
        priority,
        len(tool.required_args),
        0 if tool.light_probe else 1,
        tool.server_id,
        tool.tool_name,
    )


def select_mcp_tools(
    tools: list[McpToolDescriptor],
    context: dict[str, Any],
    *,
    allowlist: list[str] | None,
    max_tools: int,
    mode: str,
    light_probe_only: bool = False,
) -> tuple[list[PlannedMcpTool], list[dict[str, Any]]]:
    filtered = filter_tools_by_allowlist(tools, allowlist)
    filtered = [tool for tool in filtered if tool.read_only]
    if light_probe_only:
        filtered = [tool for tool in filtered if tool.light_probe]

    selected: list[PlannedMcpTool] = []
    skipped: list[dict[str, Any]] = []

    for tool in sorted(filtered, key=lambda item: _tool_priority(item, mode)):
        args, missing = derive_tool_arguments(tool, context)
        fqdn = f"mcp.{tool.server_id}.{tool.tool_name}"
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

        selected.append(PlannedMcpTool(descriptor=tool, arguments=args))
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
) -> tuple[InvestigationPlan, list[dict[str, Any]]]:
    selected, skipped = select_mcp_tools(
        tools,
        context,
        allowlist=allowlist,
        max_tools=max_steps,
        mode="evidence",
        light_probe_only=False,
    )
    if not selected:
        selected, discovery_skipped = select_mcp_tools(
            tools,
            context,
            allowlist=allowlist,
            max_tools=max_steps,
            mode="discovery",
            light_probe_only=True,
        )
        skipped.extend(discovery_skipped)

    steps: list[PlanStep] = []
    for item in selected:
        tool = item.descriptor
        timeout_seconds = 90 if tool.light_probe else 150
        budget_weight = 1 if tool.light_probe else 2
        capability = f"{tool.server_id}.{tool.tool_name}"

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
            )
        )

    plan = InvestigationPlan(
        investigation_id=investigation_id,
        ordered_steps=steps,
        max_api_calls=max(1, max(max_api_calls, len(steps))),
        max_stage_wall_clock_seconds=max(30, max_stage_wall_clock_seconds),
    )
    return plan, skipped
