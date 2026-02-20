from __future__ import annotations

from typing import Any

import httpx

from platform_core.models import McpServerConfig, McpToolDescriptor


class McpClientError(RuntimeError):
    pass


def _base_url(url: str) -> str:
    return url.rstrip("/")


def test_mcp_server(config: McpServerConfig) -> tuple[bool, str]:
    base = _base_url(config.base_url)
    timeout = config.timeout_seconds
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{base}/health")
            if response.status_code < 400:
                return True, "MCP server health check passed."
            response = client.get(f"{base}/tools")
            if response.status_code < 400:
                return True, "MCP server reachable and tool catalog available."
            return False, f"MCP server returned status {response.status_code}."
    except Exception as exc:
        return False, f"MCP connection failed: {exc}"


def discover_mcp_tools(config: McpServerConfig) -> list[McpToolDescriptor]:
    base = _base_url(config.base_url)
    timeout = config.timeout_seconds
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{base}/tools")
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        raise McpClientError(f"Unable to discover MCP tools for {config.server_id}: {exc}") from exc

    entries: list[dict[str, Any]]
    if isinstance(payload, dict) and isinstance(payload.get("tools"), list):
        entries = payload["tools"]
    elif isinstance(payload, list):
        entries = payload
    else:
        entries = []

    tools: list[McpToolDescriptor] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("tool_name") or "").strip()
        if not name:
            continue
        tools.append(
            McpToolDescriptor(
                server_id=config.server_id,
                tool_name=name,
                description=str(entry.get("description")) if entry.get("description") else None,
                capabilities=[str(item) for item in entry.get("capabilities", []) if isinstance(item, str)],
                read_only=bool(entry.get("read_only", True)),
                light_probe=bool(entry.get("light_probe", False)),
            )
        )
    return tools


def invoke_mcp_tool(config: McpServerConfig, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    base = _base_url(config.base_url)
    timeout = config.timeout_seconds
    payload = {"tool_name": tool_name, "arguments": arguments}
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"{base}/invoke", json=payload)
            if response.status_code == 404:
                response = client.post(f"{base}/tools/{tool_name}", json=arguments)
            response.raise_for_status()
            result = response.json()
    except Exception as exc:
        raise McpClientError(f"MCP invoke failed for {tool_name}: {exc}") from exc

    if isinstance(result, dict):
        return result
    return {"result": result}
