from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from platform_core.mcp_client import discover_mcp_tools, invoke_mcp_tool, test_mcp_server as run_mcp_server_test
from platform_core.models import McpServerConfig


def _config() -> McpServerConfig:
    return McpServerConfig(
        server_id="newrelic",
        tenant="default",
        environment="prod",
        transport="http_sse",
        base_url="https://mcp.newrelic.com/mcp/",
        secret_ref_name="mcp-secret",
        secret_ref_key="NEW_RELIC_API_KEY",
        timeout_seconds=8,
        enabled=True,
        updated_at=datetime.now(timezone.utc),
        updated_by="test",
    )


def test_discover_tools_uses_streamable_http_and_auth_headers(monkeypatch) -> None:
    config = _config()
    monkeypatch.setenv("NEW_RELIC_API_KEY", "nr-test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode() or "{}")
        method = payload.get("method")

        if method == "initialize":
            assert request.headers.get("authorization") == "Bearer nr-test-token"
            assert request.headers.get("api-key") == "nr-test-token"
            return httpx.Response(
                status_code=200,
                headers={"Mcp-Session-Id": "session-1"},
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
                },
            )

        if method == "notifications/initialized":
            assert request.headers.get("mcp-session-id") == "session-1"
            return httpx.Response(status_code=202, text="")

        if method == "tools/list":
            assert request.headers.get("mcp-session-id") == "session-1"
            return httpx.Response(
                status_code=200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "nr.entity.search",
                                "description": "Search New Relic entities.",
                                "annotations": {"readOnlyHint": True},
                                "tags": ["newrelic", "entity"],
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                                    "required": ["query"],
                                },
                            }
                        ]
                    },
                },
            )

        return httpx.Response(status_code=404, json={"error": "unknown method"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "platform_core.mcp_client._build_client",
        lambda timeout: httpx.Client(transport=transport, timeout=timeout),
    )

    tools = discover_mcp_tools(config)
    assert len(tools) == 1
    assert tools[0].tool_name == "nr.entity.search"
    assert tools[0].read_only is True
    assert "newrelic" in tools[0].capabilities
    assert tools[0].required_args == ["query"]
    assert tools[0].arg_keys == ["limit", "query"]


def test_invoke_tool_uses_tools_call(monkeypatch) -> None:
    config = _config()
    monkeypatch.setenv("NEW_RELIC_API_KEY", "nr-test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode() or "{}")
        method = payload.get("method")

        if method == "initialize":
            return httpx.Response(
                status_code=200,
                headers={"Mcp-Session-Id": "session-2"},
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
                },
            )

        if method == "notifications/initialized":
            return httpx.Response(status_code=202, text="")

        if method == "tools/call":
            assert payload["params"]["name"] == "nr.query.nrql"
            assert payload["params"]["arguments"] == {"nrql": "SELECT 1"}
            return httpx.Response(
                status_code=200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"content": [{"type": "text", "text": "ok"}], "isError": False},
                },
            )

        return httpx.Response(status_code=404, json={"error": "unknown method"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "platform_core.mcp_client._build_client",
        lambda timeout: httpx.Client(transport=transport, timeout=timeout),
    )

    result = invoke_mcp_tool(config, "nr.query.nrql", {"nrql": "SELECT 1"})
    assert result["isError"] is False
    assert result["content"][0]["text"] == "ok"


def test_discover_tools_falls_back_to_legacy_endpoint(monkeypatch) -> None:
    config = _config().model_copy(update={"base_url": "https://legacy-mcp.example.com"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(status_code=404, json={"detail": "not found"})
        if request.method == "GET" and request.url.path == "/tools":
            return httpx.Response(status_code=200, json=[{"name": "legacy_tool"}])
        return httpx.Response(status_code=404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "platform_core.mcp_client._build_client",
        lambda timeout: httpx.Client(transport=transport, timeout=timeout),
    )

    tools = discover_mcp_tools(config)
    assert [tool.tool_name for tool in tools] == ["legacy_tool"]


def test_discover_tools_infers_read_only_and_light_probe(monkeypatch) -> None:
    config = _config()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode() or "{}")
        method = payload.get("method")
        if method == "initialize":
            return httpx.Response(
                status_code=200,
                headers={"Mcp-Session-Id": "session-3"},
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
                },
            )
        if method == "notifications/initialized":
            return httpx.Response(status_code=202, text="")
        if method == "tools/list":
            return httpx.Response(
                status_code=200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "tools": [
                            {"name": "create_dashboard", "description": "mutating write tool"},
                            {"name": "list_datasources", "description": "safe catalog listing"},
                        ]
                    },
                },
            )
        return httpx.Response(status_code=404, json={"detail": "unknown method"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "platform_core.mcp_client._build_client",
        lambda timeout: httpx.Client(transport=transport, timeout=timeout),
    )

    tools = discover_mcp_tools(config)
    by_name = {tool.tool_name: tool for tool in tools}
    assert by_name["create_dashboard"].read_only is False
    assert by_name["create_dashboard"].light_probe is False
    assert by_name["list_datasources"].read_only is True
    assert by_name["list_datasources"].light_probe is True


def test_mcp_test_endpoint_reports_auth_fail_without_false_health_pass(monkeypatch) -> None:
    config = _config()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(status_code=401, json={"error": "unauthorized"})
        if request.method == "GET" and request.url.path == "/mcp/health":
            return httpx.Response(status_code=200, json={"status": "ok"})
        return httpx.Response(status_code=404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "platform_core.mcp_client._build_client",
        lambda timeout: httpx.Client(transport=transport, timeout=timeout),
    )

    success, detail = run_mcp_server_test(config)
    assert success is False
    assert "authentication failed" in detail.lower()
