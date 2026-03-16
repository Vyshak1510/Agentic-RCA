from __future__ import annotations

import os

from platform_core.mcp_planning import build_mcp_only_plan, derive_tool_arguments, select_mcp_tools
from platform_core.models import ArtifactState, McpExecutionPhase, McpScopeKind, McpToolDescriptor


def test_select_mcp_tools_skips_get_tools_without_context_args() -> None:
    tools = [
        McpToolDescriptor(
            server_id="grafana",
            tool_name="get_datasource",
            description="requires uid or name",
            capabilities=[],
            read_only=True,
            light_probe=True,
            arg_keys=["uid", "name"],
            required_args=[],
        )
    ]
    selected, skipped = select_mcp_tools(
        tools,
        context={},
        allowlist=["mcp.grafana.*"],
        max_tools=3,
        mode="evidence",
        light_probe_only=False,
    )
    assert not selected
    assert skipped
    assert skipped[0]["reason"] == "missing_context_args"


def test_select_mcp_tools_allows_get_tools_when_context_args_present() -> None:
    tools = [
        McpToolDescriptor(
            server_id="grafana",
            tool_name="get_datasource",
            description="requires uid or name",
            capabilities=[],
            read_only=True,
            light_probe=True,
            arg_keys=["uid", "name"],
            required_args=[],
        )
    ]
    selected, skipped = select_mcp_tools(
        tools,
        context={"uid": "prometheus"},
        allowlist=["mcp.grafana.*"],
        max_tools=3,
        mode="evidence",
        light_probe_only=False,
    )
    assert not skipped
    assert selected
    assert selected[0].arguments == {"uid": "prometheus"}


def test_select_mcp_tools_disables_grafana_oncall_tools_by_default() -> None:
    tools = [
        McpToolDescriptor(
            server_id="grafana",
            tool_name="list_alert_groups",
            description="oncall groups",
            capabilities=[],
            read_only=True,
            light_probe=True,
            arg_keys=[],
            required_args=[],
        )
    ]
    selected, skipped = select_mcp_tools(
        tools,
        context={},
        allowlist=["mcp.grafana.*"],
        max_tools=3,
        mode="evidence",
        light_probe_only=False,
    )
    assert not selected
    assert skipped
    assert skipped[0]["reason"] == "disabled_by_policy"


def test_select_mcp_tools_can_enable_grafana_oncall_tools_with_flag() -> None:
    os.environ["RCA_GRAFANA_ENABLE_ONCALL_TOOLS"] = "1"
    try:
        tools = [
            McpToolDescriptor(
                server_id="grafana",
                tool_name="list_alert_groups",
                description="oncall groups",
                capabilities=[],
                read_only=True,
                light_probe=True,
                arg_keys=[],
                required_args=[],
            )
        ]
        selected, skipped = select_mcp_tools(
            tools,
            context={},
            allowlist=["mcp.grafana.*"],
            max_tools=3,
            mode="evidence",
            light_probe_only=False,
        )
        assert selected
        assert not skipped
    finally:
        os.environ.pop("RCA_GRAFANA_ENABLE_ONCALL_TOOLS", None)


def test_derive_tool_arguments_keeps_service_from_alert_context() -> None:
    tool = McpToolDescriptor(
        server_id="jaeger",
        tool_name="search_traces",
        description=None,
        capabilities=[],
        read_only=True,
        light_probe=False,
        arg_keys=["service"],
        required_args=["service"],
    )
    args, missing = derive_tool_arguments(tool, {"service": "recommendationservice"})
    assert not missing
    assert args["service"] == "recommendationservice"


def test_derive_tool_arguments_coerces_tags_to_list() -> None:
    tool = McpToolDescriptor(
        server_id="grafana",
        tool_name="get_annotations",
        description=None,
        capabilities=[],
        read_only=True,
        light_probe=True,
        arg_keys=["Tags"],
        required_args=[],
    )
    args, missing = derive_tool_arguments(tool, {"service": "recommendationservice"})
    assert not missing
    assert args["Tags"] == ["recommendationservice"]


def test_derive_tool_arguments_keeps_singular_tag_as_string() -> None:
    tool = McpToolDescriptor(
        server_id="grafana",
        tool_name="get_annotation_tags",
        description=None,
        capabilities=[],
        read_only=True,
        light_probe=True,
        arg_keys=["tag"],
        required_args=[],
    )
    args, missing = derive_tool_arguments(tool, {"service": "recommendationservice"})
    assert not missing
    assert args["tag"] == "recommendationservice"


def test_derive_tool_arguments_does_not_infer_invalid_jaeger_tags_from_service() -> None:
    tool = McpToolDescriptor(
        server_id="jaeger",
        tool_name="search_traces",
        description=None,
        capabilities=[],
        read_only=True,
        light_probe=True,
        arg_keys=["service", "tags"],
        required_args=["service"],
    )
    args, missing = derive_tool_arguments(tool, {"service": "recommendationservice"})
    assert not missing
    assert args["service"] == "recommendationservice"
    assert "tags" not in args


def test_service_scoped_tools_are_blocked_without_resolved_service() -> None:
    tools = [
        McpToolDescriptor(
            server_id="jaeger",
            tool_name="get_services",
            description="discover services",
            capabilities=[],
            read_only=True,
            light_probe=True,
            arg_keys=[],
            required_args=[],
            phase=McpExecutionPhase.DISCOVER,
            scope_kind=McpScopeKind.GLOBAL,
            produces_artifacts=["service_candidates"],
            default_priority=0,
        ),
        McpToolDescriptor(
            server_id="jaeger",
            tool_name="search_traces",
            description="trace search",
            capabilities=[],
            read_only=True,
            light_probe=False,
            arg_keys=["service"],
            required_args=["service"],
            phase=McpExecutionPhase.INSPECT,
            scope_kind=McpScopeKind.SERVICE,
            requires_artifacts=["resolved_service"],
            produces_artifacts=["trace_ids"],
            default_priority=10,
        ),
    ]
    selected, skipped = select_mcp_tools(
        tools,
        context={},
        allowlist=["mcp.jaeger.*"],
        max_tools=3,
        mode="evidence",
        light_probe_only=False,
        artifact_state=ArtifactState(alert_terms=["recommendationservice"]),
        alert_payload={
            "source": "manual",
            "severity": "critical",
            "incident_key": "inc-1",
            "entity_ids": ["recommendationservice"],
            "timestamps": {},
            "raw_payload": {},
        },
    )
    assert [item.descriptor.tool_name for item in selected] == ["get_services"]
    assert any(item["reason"] == "missing_artifacts" and item["tool_name"] == "mcp.jaeger.search_traces" for item in skipped)


def test_select_mcp_tools_binds_resolved_service_alias() -> None:
    tool = McpToolDescriptor(
        server_id="jaeger",
        tool_name="search_traces",
        description="trace search",
        capabilities=[],
        read_only=True,
        light_probe=False,
        arg_keys=["service"],
        required_args=["service"],
        phase=McpExecutionPhase.INSPECT,
        scope_kind=McpScopeKind.SERVICE,
        requires_artifacts=["resolved_service"],
        produces_artifacts=["trace_ids"],
        default_priority=10,
    )
    selected, skipped = select_mcp_tools(
        [tool],
        context={"service": "recommendationservice"},
        allowlist=["mcp.jaeger.*"],
        max_tools=1,
        mode="evidence",
        artifact_state=ArtifactState(
            alert_terms=["recommendationservice"],
            resolved_service="recommendation",
            service_candidates=["recommendation"],
        ),
        alert_payload={
            "source": "manual",
            "severity": "critical",
            "incident_key": "inc-2",
            "entity_ids": ["recommendationservice"],
            "timestamps": {},
            "raw_payload": {},
        },
    )
    assert not skipped
    assert selected
    assert selected[0].arguments["service"] == "recommendation"


def test_prometheus_query_range_is_shaped_from_resolved_service() -> None:
    tool = McpToolDescriptor(
        server_id="prometheus",
        tool_name="query_range",
        description="query range",
        capabilities=[],
        read_only=True,
        light_probe=False,
        arg_keys=["query", "start", "end", "step"],
        required_args=["query", "start", "end"],
        phase=McpExecutionPhase.INSPECT,
        scope_kind=McpScopeKind.METRIC,
        requires_artifacts=["resolved_service"],
        produces_artifacts=["root_cause_signals"],
        default_priority=20,
    )
    selected, skipped = select_mcp_tools(
        [tool],
        context={},
        allowlist=["mcp.prometheus.*"],
        max_tools=1,
        mode="evidence",
        artifact_state=ArtifactState(
            alert_terms=["high error rate", "recommendationservice"],
            resolved_service="recommendation",
            metric_label_keys=["service_name"],
        ),
        alert_payload={
            "source": "manual",
            "severity": "critical",
            "incident_key": "inc-3",
            "entity_ids": ["recommendationservice"],
            "timestamps": {},
            "raw_payload": {"title": "high error rate on recommendationservice"},
        },
    )
    assert not skipped
    assert selected
    assert "recommendation" in selected[0].arguments["query"]
    assert "STATUS_CODE_ERROR" in selected[0].arguments["query"]
    assert selected[0].arguments["step"] == "60s"


def test_build_mcp_only_plan_does_not_emit_service_scoped_steps_when_unresolved() -> None:
    tool = McpToolDescriptor(
        server_id="jaeger",
        tool_name="search_traces",
        description="trace search",
        capabilities=[],
        read_only=True,
        light_probe=False,
        arg_keys=["service"],
        required_args=["service"],
        phase=McpExecutionPhase.INSPECT,
        scope_kind=McpScopeKind.SERVICE,
        requires_artifacts=["resolved_service"],
        produces_artifacts=["trace_ids"],
        default_priority=10,
    )
    plan, skipped = build_mcp_only_plan(
        investigation_id="inv-1",
        tools=[tool],
        context={},
        allowlist=["mcp.jaeger.*"],
        max_steps=3,
        max_api_calls=10,
        max_stage_wall_clock_seconds=600,
        artifact_state=ArtifactState(alert_terms=["recommendationservice"]),
        alert_payload={
            "source": "manual",
            "severity": "critical",
            "incident_key": "inc-4",
            "entity_ids": ["recommendationservice"],
            "timestamps": {},
            "raw_payload": {},
        },
    )
    assert not plan.ordered_steps
    assert any(item["reason"] == "missing_artifacts" for item in skipped)


def test_build_mcp_only_plan_prioritizes_service_scoped_inspect_steps_after_resolution() -> None:
    tools = [
        McpToolDescriptor(
            server_id="grafana",
            tool_name="list_datasources",
            description="discover datasources",
            capabilities=[],
            read_only=True,
            light_probe=True,
            arg_keys=[],
            required_args=[],
            phase=McpExecutionPhase.DISCOVER,
            scope_kind=McpScopeKind.GLOBAL,
            default_priority=0,
        ),
        McpToolDescriptor(
            server_id="jaeger",
            tool_name="get_operations",
            description="discover service operations",
            capabilities=[],
            read_only=True,
            light_probe=False,
            arg_keys=["service"],
            required_args=["service"],
            phase=McpExecutionPhase.INSPECT,
            scope_kind=McpScopeKind.SERVICE,
            requires_artifacts=["resolved_service"],
            produces_artifacts=["operation_candidates"],
            default_priority=10,
        ),
        McpToolDescriptor(
            server_id="prometheus",
            tool_name="query_range",
            description="query service metrics",
            capabilities=[],
            read_only=True,
            light_probe=False,
            arg_keys=["query", "start", "end"],
            required_args=["query", "start", "end"],
            phase=McpExecutionPhase.INSPECT,
            scope_kind=McpScopeKind.METRIC,
            requires_artifacts=["resolved_service"],
            produces_artifacts=["root_cause_signals"],
            default_priority=20,
        ),
    ]
    plan, skipped = build_mcp_only_plan(
        investigation_id="inv-2",
        tools=tools,
        context={},
        allowlist=["mcp.grafana.*", "mcp.jaeger.*", "mcp.prometheus.*"],
        max_steps=2,
        max_api_calls=10,
        max_stage_wall_clock_seconds=600,
        artifact_state=ArtifactState(
            alert_terms=["recommendationservice"],
            resolved_service="recommendation",
            service_candidates=["recommendation"],
            metric_label_keys=["service_name"],
        ),
        alert_payload={
            "source": "manual",
            "severity": "critical",
            "incident_key": "inc-5",
            "entity_ids": ["recommendationservice"],
            "timestamps": {},
            "raw_payload": {
                "title": "Recommendation latency is elevated",
                "service": "recommendationservice",
            },
        },
    )

    step_names = [step.mcp_tool_name for step in plan.ordered_steps]
    assert "get_operations" in step_names
    assert "query_range" in step_names
    assert "list_datasources" not in step_names
    assert not skipped
