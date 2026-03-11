from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

import httpx

from platform_core.models import McpServerConfig, McpToolDescriptor


class McpClientError(RuntimeError):
    pass


def _base_url(url: str) -> str:
    return url.rstrip("/")


def _build_client(timeout: int) -> httpx.Client:
    return httpx.Client(timeout=timeout)


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _looks_mutating_tool(tool_name: str) -> bool:
    lowered = tool_name.strip().lower()
    mutating_prefixes = (
        "create_",
        "update_",
        "delete_",
        "add_",
        "set_",
        "patch_",
        "write_",
        "ack_",
        "resolve_",
        "silence_",
        "unsilence_",
        "mute_",
        "unmute_",
    )
    return lowered.startswith(mutating_prefixes)


def _infer_light_probe(tool_name: str, read_only: bool) -> bool:
    if not read_only:
        return False
    lowered = tool_name.strip().lower()
    if lowered.startswith(("list_", "get_", "search_")):
        return True
    if lowered.startswith(("query_", "fetch_", "render_")):
        return False
    return False


def _extract_schema(entry: dict[str, Any]) -> dict[str, Any]:
    schema = entry.get("inputSchema")
    if isinstance(schema, dict):
        return schema
    schema = entry.get("input_schema")
    if isinstance(schema, dict):
        return schema
    return {}


def _extract_schema_keys(schema: dict[str, Any]) -> tuple[list[str], list[str]]:
    properties = schema.get("properties")
    arg_keys: list[str] = []
    if isinstance(properties, dict):
        arg_keys = [str(key) for key in properties.keys() if str(key).strip()]
    required = schema.get("required")
    required_args = [str(item) for item in required if isinstance(item, str) and item.strip()] if isinstance(required, list) else []
    return sorted(set(arg_keys)), sorted(set(required_args))


def _resolve_secret_value(config: McpServerConfig) -> str | None:
    key_name = (config.secret_ref_key or "").strip()
    secret_name = (config.secret_ref_name or "").strip()

    if key_name:
        direct = os.getenv(key_name)
        if direct:
            return direct

    if secret_name and key_name:
        for candidate in (f"{secret_name}__{key_name}", f"{secret_name}_{key_name}"):
            value = os.getenv(candidate)
            if value:
                return value

        blob = os.getenv(secret_name)
        if blob:
            try:
                parsed = json.loads(blob)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                nested = parsed.get(key_name)
                if isinstance(nested, (str, int, float)):
                    return str(nested)
    return None


def _optional_mcp_header(config: McpServerConfig, header_key: str) -> str | None:
    normalized_server = config.server_id.upper().replace("-", "_")
    normalized_key = header_key.upper().replace("-", "_")
    candidates = [
        f"MCP_{normalized_server}_{normalized_key}",
        f"{normalized_server}_MCP_{normalized_key}",
        f"MCP_{normalized_key}",
    ]
    for env_key in candidates:
        value = os.getenv(env_key)
        if value:
            return value
    return None


def _auth_headers(config: McpServerConfig) -> dict[str, str]:
    token = _resolve_secret_value(config)
    headers: dict[str, str] = {}
    if token:
        # Support both common patterns across MCP providers.
        headers["Authorization"] = f"Bearer {token}"
        headers["Api-Key"] = token

    include_tags = _optional_mcp_header(config, "include-tags")
    if include_tags:
        headers["include-tags"] = include_tags
    return headers


def _extract_jsonrpc_message_payloads(response: httpx.Response) -> list[dict[str, Any]]:
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" in content_type:
        payloads: list[dict[str, Any]] = []
        data_lines: list[str] = []
        for line in response.text.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
                continue
            if line.strip():
                continue
            if not data_lines:
                continue
            blob = "\n".join(data_lines)
            data_lines = []
            try:
                parsed = json.loads(blob)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                payloads.append(parsed)
        if data_lines:
            blob = "\n".join(data_lines)
            try:
                parsed = json.loads(blob)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                payloads.append(parsed)
        return payloads

    if not response.text.strip():
        return []
    parsed = response.json()
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def _jsonrpc_error(payload: dict[str, Any]) -> str | None:
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    message = error.get("message")
    if code is None and message is None:
        return "unknown JSON-RPC error"
    return f"{code}: {message}"


def _is_auth_failure(exc: Exception) -> bool:
    text = str(exc).lower()
    return "401" in text or "403" in text or "unauthorized" in text or "forbidden" in text


class _StreamableHttpSession:
    def __init__(self, config: McpServerConfig) -> None:
        self._config = config
        self._base_url = _base_url(config.base_url)
        self._protocol_version = os.getenv("MCP_PROTOCOL_VERSION", "2025-06-18")
        self._session_id: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        headers.update(_auth_headers(self._config))
        headers["MCP-Protocol-Version"] = self._protocol_version
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, client: httpx.Client, payload: dict[str, Any]) -> list[dict[str, Any]]:
        response = client.post(self._base_url, json=payload, headers=self._headers())
        response.raise_for_status()
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id
        return _extract_jsonrpc_message_payloads(response)

    @staticmethod
    def _result_for_id(payloads: list[dict[str, Any]], request_id: str, method: str) -> Any:
        for payload in payloads:
            if payload.get("id") != request_id:
                continue
            error_text = _jsonrpc_error(payload)
            if error_text:
                raise McpClientError(f"MCP method {method} failed: {error_text}")
            if "result" not in payload:
                raise McpClientError(f"MCP method {method} returned no result.")
            return payload["result"]

        # Some implementations return a single payload without JSON-RPC envelope.
        if len(payloads) == 1 and "result" not in payloads[0]:
            return payloads[0]

        raise McpClientError(f"MCP method {method} returned no response for request id {request_id}.")

    def initialize(self, client: httpx.Client) -> None:
        request_id = f"init-{uuid4().hex}"
        init_payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": self._protocol_version,
                "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
                "clientInfo": {"name": "agentic-rca-platform", "version": "0.1.0"},
            },
        }
        result = self._result_for_id(self._post(client, init_payload), request_id, "initialize")
        if isinstance(result, dict):
            negotiated = result.get("protocolVersion")
            if isinstance(negotiated, str) and negotiated.strip():
                self._protocol_version = negotiated.strip()

        initialized_notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        self._post(client, initialized_notification)

    def call(self, client: httpx.Client, method: str, params: dict[str, Any] | None = None) -> Any:
        request_id = f"req-{uuid4().hex}"
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        return self._result_for_id(self._post(client, payload), request_id, method)


def _discover_tools_via_mcp(config: McpServerConfig) -> list[McpToolDescriptor]:
    timeout = config.timeout_seconds
    with _build_client(timeout=timeout) as client:
        session = _StreamableHttpSession(config)
        session.initialize(client)
        result = session.call(client, "tools/list", {})

    entries: list[dict[str, Any]]
    if isinstance(result, dict) and isinstance(result.get("tools"), list):
        entries = [item for item in result["tools"] if isinstance(item, dict)]
    elif isinstance(result, list):
        entries = [item for item in result if isinstance(item, dict)]
    else:
        entries = []

    tools: list[McpToolDescriptor] = []
    for entry in entries:
        name = str(entry.get("name") or entry.get("tool_name") or "").strip()
        if not name:
            continue
        schema = _extract_schema(entry)
        arg_keys, required_args = _extract_schema_keys(schema)
        annotations = entry.get("annotations", {})
        if not isinstance(annotations, dict):
            annotations = {}

        read_only_hint = entry.get("read_only")
        if read_only_hint is None:
            read_only_hint = entry.get("readOnly")
        if read_only_hint is None:
            read_only_hint = annotations.get("readOnlyHint")
        if read_only_hint is None:
            read_only = not _looks_mutating_tool(name)
        else:
            read_only = _coerce_bool(read_only_hint, not _looks_mutating_tool(name))

        light_probe_hint = entry.get("light_probe")
        if light_probe_hint is None:
            light_probe_hint = entry.get("lightProbe")
        if light_probe_hint is None:
            light_probe_hint = annotations.get("lightProbeHint")
        if light_probe_hint is None:
            light_probe = _infer_light_probe(name, read_only)
        else:
            light_probe = _coerce_bool(light_probe_hint, _infer_light_probe(name, read_only))

        capabilities: list[str] = []
        for raw in (
            entry.get("capabilities"),
            entry.get("tags"),
            annotations.get("category"),
        ):
            if isinstance(raw, str):
                capabilities.append(raw)
            elif isinstance(raw, list):
                capabilities.extend([item for item in raw if isinstance(item, str)])

        tools.append(
            McpToolDescriptor(
                server_id=config.server_id,
                tool_name=name,
                description=str(entry.get("description")) if entry.get("description") else None,
                capabilities=sorted(set(capabilities)),
                read_only=read_only,
                light_probe=light_probe,
                arg_keys=arg_keys,
                required_args=required_args,
            )
        )
    return tools


def _invoke_tool_via_mcp(config: McpServerConfig, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    timeout = config.timeout_seconds
    with _build_client(timeout=timeout) as client:
        session = _StreamableHttpSession(config)
        session.initialize(client)
        result = session.call(client, "tools/call", {"name": tool_name, "arguments": arguments})

    if isinstance(result, dict):
        if _coerce_bool(result.get("isError"), False):
            raise McpClientError(f"MCP tool {tool_name} returned isError=true")
        return result
    return {"result": result}


def _discover_tools_legacy(config: McpServerConfig) -> list[McpToolDescriptor]:
    base = _base_url(config.base_url)
    timeout = config.timeout_seconds
    with _build_client(timeout=timeout) as client:
        response = client.get(f"{base}/tools", headers=_auth_headers(config))
        response.raise_for_status()
        payload = response.json()

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
        schema = _extract_schema(entry)
        arg_keys, required_args = _extract_schema_keys(schema)
        read_only_hint = entry.get("read_only")
        if read_only_hint is None:
            read_only_hint = entry.get("readOnly")
        if read_only_hint is None:
            read_only = not _looks_mutating_tool(name)
        else:
            read_only = _coerce_bool(read_only_hint, not _looks_mutating_tool(name))

        light_probe_hint = entry.get("light_probe")
        if light_probe_hint is None:
            light_probe_hint = entry.get("lightProbe")
        if light_probe_hint is None:
            light_probe = _infer_light_probe(name, read_only)
        else:
            light_probe = _coerce_bool(light_probe_hint, _infer_light_probe(name, read_only))

        tools.append(
            McpToolDescriptor(
                server_id=config.server_id,
                tool_name=name,
                description=str(entry.get("description")) if entry.get("description") else None,
                capabilities=[str(item) for item in entry.get("capabilities", []) if isinstance(item, str)],
                read_only=read_only,
                light_probe=light_probe,
                arg_keys=arg_keys,
                required_args=required_args,
            )
        )
    return tools


def _invoke_tool_legacy(config: McpServerConfig, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    base = _base_url(config.base_url)
    timeout = config.timeout_seconds
    payload = {"tool_name": tool_name, "arguments": arguments}
    with _build_client(timeout=timeout) as client:
        response = client.post(f"{base}/invoke", json=payload, headers=_auth_headers(config))
        if response.status_code == 404:
            response = client.post(f"{base}/tools/{tool_name}", json=arguments, headers=_auth_headers(config))
        response.raise_for_status()
        result = response.json()
    if isinstance(result, dict):
        return result
    return {"result": result}


def test_mcp_server(config: McpServerConfig) -> tuple[bool, str]:
    try:
        tools = _discover_tools_via_mcp(config)
        return True, f"MCP server reachable via streamable HTTP ({len(tools)} tools discovered)."
    except Exception as mcp_exc:
        if _is_auth_failure(mcp_exc):
            return False, f"MCP authentication failed: {mcp_exc}"
        base = _base_url(config.base_url)
        timeout = config.timeout_seconds
        try:
            with _build_client(timeout=timeout) as client:
                for health_path in ("/healthz", "/health"):
                    response = client.get(f"{base}{health_path}", headers=_auth_headers(config))
                    if response.status_code < 400:
                        return True, f"MCP server health check passed ({health_path})."
                response = client.get(f"{base}/tools", headers=_auth_headers(config))
                if response.status_code < 400:
                    return True, "MCP server reachable and tool catalog available."
                return False, f"MCP server returned status {response.status_code}. ({mcp_exc})"
        except Exception as legacy_exc:
            return False, f"MCP connection failed: {mcp_exc}; legacy check failed: {legacy_exc}"


def discover_mcp_tools(config: McpServerConfig) -> list[McpToolDescriptor]:
    try:
        return _discover_tools_via_mcp(config)
    except Exception as mcp_exc:
        try:
            return _discover_tools_legacy(config)
        except Exception as legacy_exc:
            raise McpClientError(
                f"Unable to discover MCP tools for {config.server_id}: {mcp_exc}; legacy fallback failed: {legacy_exc}"
            ) from legacy_exc


def invoke_mcp_tool(config: McpServerConfig, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        return _invoke_tool_via_mcp(config, tool_name, arguments)
    except Exception as mcp_exc:
        try:
            return _invoke_tool_legacy(config, tool_name, arguments)
        except Exception as legacy_exc:
            raise McpClientError(
                f"MCP invoke failed for {tool_name}: {mcp_exc}; legacy fallback failed: {legacy_exc}"
            ) from legacy_exc
