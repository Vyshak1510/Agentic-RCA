from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rca_plugin_sdk.interfaces import ConnectorPlugin

from platform_core.mcp_client import invoke_mcp_tool
from platform_core.models import McpServerConfig, McpToolDescriptor, WorkflowStageId


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    source: str
    description: str
    provider: str
    capability: str
    read_only: bool
    light_probe: bool
    arg_keys: list[str] | None = None
    required_args: list[str] | None = None


class ToolRegistry:
    def __init__(
        self,
        connectors: list[ConnectorPlugin],
        mcp_servers: list[McpServerConfig] | None = None,
        mcp_tools: list[McpToolDescriptor] | None = None,
    ) -> None:
        self._connectors = {connector.manifest.provider: connector for connector in connectors}
        self._mcp_servers = {server.server_id: server for server in (mcp_servers or []) if server.enabled}
        self._definitions: dict[str, ToolDefinition] = {}
        self._register_builtin_tools()
        self._register_mcp_tools(mcp_tools or [])

    def _register_builtin_tools(self) -> None:
        self._definitions["context.tool_inventory"] = ToolDefinition(
            name="context.tool_inventory",
            source="builtin",
            description="List currently available tool definitions and capabilities.",
            provider="builtin",
            capability="inventory",
            read_only=True,
            light_probe=True,
        )
        self._definitions["context.alert_entities"] = ToolDefinition(
            name="context.alert_entities",
            source="builtin",
            description="Return entity and incident identifiers from the alert envelope.",
            provider="builtin",
            capability="alert_context",
            read_only=True,
            light_probe=True,
        )

        for connector in self._connectors.values():
            provider = connector.manifest.provider
            self._definitions[f"connector.{provider}.discover_context"] = ToolDefinition(
                name=f"connector.{provider}.discover_context",
                source="connector",
                description=f"Discover lightweight context from {provider}.",
                provider=provider,
                capability="discover_context",
                read_only=connector.manifest.read_only,
                light_probe=True,
            )
            for capability in connector.manifest.capabilities:
                self._definitions[f"connector.{provider}.collect.{capability}"] = ToolDefinition(
                    name=f"connector.{provider}.collect.{capability}",
                    source="connector",
                    description=f"Collect {capability} signals from {provider}.",
                    provider=provider,
                    capability=capability,
                    read_only=connector.manifest.read_only,
                    light_probe=False,
                )

    def _register_mcp_tools(self, tools: list[McpToolDescriptor]) -> None:
        for tool in tools:
            server = self._mcp_servers.get(tool.server_id)
            if not server:
                continue
            name = f"mcp.{tool.server_id}.{tool.tool_name}"
            self._definitions[name] = ToolDefinition(
                name=name,
                source="mcp",
                description=tool.description or f"MCP tool {tool.tool_name}",
                provider=tool.server_id,
                capability="mcp",
                read_only=tool.read_only,
                light_probe=tool.light_probe,
                arg_keys=tool.arg_keys,
                required_args=tool.required_args,
            )

    def list_tools(
        self,
        *,
        stage_id: WorkflowStageId,
        allowlist: list[str] | None = None,
        light_probe_only: bool = False,
    ) -> list[ToolDefinition]:
        allow = [entry.strip() for entry in (allowlist or []) if entry and entry.strip()]

        def _is_allowed(tool_name: str) -> bool:
            if not allow:
                return True
            for entry in allow:
                if entry.endswith("*"):
                    prefix = entry[:-1]
                    if tool_name.startswith(prefix):
                        return True
                    continue
                if tool_name == entry:
                    return True
            return False

        # v1 policy: only expose read-only tools to agent stages.
        tools = [tool for tool in self._definitions.values() if tool.read_only]
        tools = [tool for tool in tools if _is_allowed(tool.name)]
        if stage_id == WorkflowStageId.BUILD_INVESTIGATION_PLAN:
            light_probe_only = True
        if light_probe_only:
            tools = [tool for tool in tools if tool.light_probe]
        return sorted(tools, key=lambda item: item.name)

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        definition = self._definitions.get(tool_name)
        if not definition:
            raise ValueError(f"Tool not found: {tool_name}")
        if not definition.read_only:
            raise ValueError(f"Tool is not read-only: {tool_name}")

        if tool_name == "context.tool_inventory":
            return {
                "tools": [
                    {
                        "name": tool.name,
                        "source": tool.source,
                        "provider": tool.provider,
                        "capability": tool.capability,
                        "read_only": tool.read_only,
                        "light_probe": tool.light_probe,
                        "arg_keys": tool.arg_keys or [],
                        "required_args": tool.required_args or [],
                    }
                    for tool in self._definitions.values()
                ]
            }
        if tool_name == "context.alert_entities":
            alert = arguments.get("alert", {})
            if not isinstance(alert, dict):
                alert = {}
            return {
                "incident_key": alert.get("incident_key"),
                "source": alert.get("source"),
                "entity_ids": alert.get("entity_ids", []),
            }

        if tool_name.startswith("connector.") and ".discover_context" in tool_name:
            provider = tool_name.split(".")[1]
            connector = self._connectors.get(provider)
            if not connector:
                return {"error": f"connector {provider} unavailable"}
            alert = arguments.get("alert", {})
            service_identity = arguments.get("service_identity", {})
            if not isinstance(alert, dict):
                alert = {}
            if not isinstance(service_identity, dict):
                service_identity = {}
            return connector.discover_context(alert, service_identity)

        if tool_name.startswith("connector.") and ".collect." in tool_name:
            provider = tool_name.split(".")[1]
            connector = self._connectors.get(provider)
            if not connector:
                return {"error": f"connector {provider} unavailable"}
            plan_step = arguments.get("plan_step", {})
            if not isinstance(plan_step, dict):
                plan_step = {}
            return {"signals": connector.collect_signals(plan_step)}

        if tool_name.startswith("mcp."):
            _, server_id, mcp_tool_name = tool_name.split(".", 2)
            server = self._mcp_servers.get(server_id)
            if not server:
                raise ValueError(f"MCP server unavailable: {server_id}")
            return invoke_mcp_tool(server, mcp_tool_name, arguments)

        raise ValueError(f"Unsupported tool: {tool_name}")
