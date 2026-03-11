from __future__ import annotations

from datetime import datetime, timezone

from platform_core.models import McpServerConfig, McpToolDescriptor, WorkflowStageId
from platform_core.tool_registry import ToolRegistry


def _server(server_id: str = "grafana") -> McpServerConfig:
    return McpServerConfig(
        server_id=server_id,
        tenant="default",
        environment="prod",
        transport="http_sse",
        base_url=f"http://{server_id}-mcp:8000/mcp",
        secret_ref_name=None,
        secret_ref_key=None,
        timeout_seconds=8,
        enabled=True,
        updated_at=datetime.now(timezone.utc),
        updated_by="test",
    )


def test_registry_exposes_only_read_only_tools_to_agent_stages() -> None:
    tools = [
        McpToolDescriptor(
            server_id="grafana",
            tool_name="list_datasources",
            description="safe",
            capabilities=[],
            read_only=True,
            light_probe=True,
        ),
        McpToolDescriptor(
            server_id="grafana",
            tool_name="create_dashboard",
            description="mutating",
            capabilities=[],
            read_only=False,
            light_probe=False,
        ),
    ]
    registry = ToolRegistry(connectors=[], mcp_servers=[_server()], mcp_tools=tools)

    resolver_tools = registry.list_tools(stage_id=WorkflowStageId.RESOLVE_SERVICE_IDENTITY)
    names = {tool.name for tool in resolver_tools}

    assert "mcp.grafana.list_datasources" in names
    assert "mcp.grafana.create_dashboard" not in names


def test_registry_allowlist_supports_prefix_wildcards() -> None:
    tools = [
        McpToolDescriptor(
            server_id="grafana",
            tool_name="list_dashboards",
            description="safe",
            capabilities=[],
            read_only=True,
            light_probe=True,
        ),
        McpToolDescriptor(
            server_id="jaeger",
            tool_name="list_services",
            description="safe",
            capabilities=[],
            read_only=True,
            light_probe=True,
        ),
    ]
    registry = ToolRegistry(connectors=[], mcp_servers=[_server("grafana"), _server("jaeger")], mcp_tools=tools)

    resolver_tools = registry.list_tools(
        stage_id=WorkflowStageId.RESOLVE_SERVICE_IDENTITY,
        allowlist=["mcp.grafana.*"],
    )
    names = {tool.name for tool in resolver_tools}
    assert "mcp.grafana.list_dashboards" in names
    assert "mcp.jaeger.list_services" not in names
