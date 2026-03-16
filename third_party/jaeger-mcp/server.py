"""Jaeger MCP server — exposes Jaeger HTTP API as MCP streamable-HTTP tools."""
from __future__ import annotations

import os
import json
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

JAEGER_BASE_URL = os.environ.get("JAEGER_BASE_URL", "http://localhost:16686").rstrip("/")
JAEGER_API_PREFIX = os.environ.get("JAEGER_API_PREFIX", "/jaeger/ui/api")
TIMEOUT = int(os.environ.get("JAEGER_TIMEOUT_SECONDS", "10"))

PROTOCOL_VERSION = "2025-06-18"

app = FastAPI(title="jaeger-mcp")

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_services",
        "description": "List all services known to Jaeger.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "annotations": {"readOnlyHint": True, "lightProbeHint": True},
    },
    {
        "name": "get_operations",
        "description": "List operations for a service in Jaeger.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service name"},
            },
            "required": ["service"],
        },
        "annotations": {"readOnlyHint": True, "lightProbeHint": True},
    },
    {
        "name": "search_traces",
        "description": (
            "Search for traces in Jaeger. Returns a list of traces matching the given filters."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service name (required)"},
                "operation": {"type": "string", "description": "Operation name filter"},
                "tags": {
                    "type": "string",
                    "description": 'URL-encoded tags filter, e.g. "error=true"',
                },
                "lookback": {
                    "type": "string",
                    "description": "Lookback window, e.g. 1h, 2h, 24h",
                    "default": "1h",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max traces to return",
                    "default": 20,
                },
                "minDuration": {"type": "string", "description": "Min duration filter, e.g. 100ms"},
                "maxDuration": {"type": "string", "description": "Max duration filter"},
            },
            "required": ["service"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_trace",
        "description": "Retrieve full span details for a single trace ID from Jaeger.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trace_id": {"type": "string", "description": "Jaeger trace ID (hex string)"},
            },
            "required": ["trace_id"],
        },
        "annotations": {"readOnlyHint": True},
    },
]


def _jaeger_get(path: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{JAEGER_BASE_URL}{JAEGER_API_PREFIX}{path}"
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        return r.json()


def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "get_services":
        data = _jaeger_get("/services")
        return data.get("data", data)

    if name == "get_operations":
        service = arguments["service"]
        data = _jaeger_get("/operations", {"service": service})
        return data.get("data", data)

    if name == "search_traces":
        params: dict[str, Any] = {"service": arguments["service"]}
        for key in ("operation", "tags", "lookback", "limit", "minDuration", "maxDuration"):
            if key in arguments:
                params[key] = arguments[key]
        params.setdefault("lookback", "1h")
        params.setdefault("limit", 20)
        data = _jaeger_get("/traces", params)
        traces = data.get("data", [])
        # Return a compact summary to stay within token limits
        summary = []
        for trace in traces[:20]:
            spans = trace.get("spans", [])
            root = spans[0] if spans else {}
            errors = sum(
                1 for sp in spans
                if any(
                    t.get("key") == "error" and t.get("value") in (True, "true")
                    for t in sp.get("tags", [])
                )
            )
            summary.append({
                "traceID": trace.get("traceID"),
                "rootSpan": root.get("operationName"),
                "rootService": (root.get("process", {}) or {}).get("serviceName"),
                "duration_us": root.get("duration"),
                "spans": len(spans),
                "errors": errors,
                "startTime": root.get("startTime"),
            })
        return summary

    if name == "get_trace":
        trace_id = arguments["trace_id"]
        data = _jaeger_get(f"/traces/{trace_id}")
        traces = data.get("data", [])
        if not traces:
            return {"error": f"trace {trace_id} not found"}
        trace = traces[0]
        spans = trace.get("spans", [])
        processes = trace.get("processes", {})
        result = []
        for sp in spans:
            proc_id = sp.get("processID", "")
            svc = (processes.get(proc_id) or {}).get("serviceName", proc_id)
            tags = {t["key"]: t["value"] for t in sp.get("tags", []) if isinstance(t, dict)}
            result.append({
                "spanID": sp.get("spanID"),
                "traceID": sp.get("traceID"),
                "operationName": sp.get("operationName"),
                "service": svc,
                "duration_us": sp.get("duration"),
                "startTime": sp.get("startTime"),
                "error": tags.get("error", False),
                "http.status_code": tags.get("http.status_code"),
                "tags": tags,
                "logs": sp.get("logs", []),
            })
        return result

    return {"error": f"unknown tool: {name}"}


def _mcp_result(request_id: str, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _mcp_error(request_id: str | None, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_mcp_error(None, -32700, "Parse error"), status_code=400)

    method = body.get("method")
    request_id = body.get("id")
    params = body.get("params") or {}

    # Notification — no response needed
    if request_id is None and method and method.startswith("notifications/"):
        return JSONResponse(status_code=202, content={})

    if method == "initialize":
        negotiated = params.get("protocolVersion", PROTOCOL_VERSION)
        session_id = uuid4().hex
        response = JSONResponse(_mcp_result(request_id, {
            "protocolVersion": negotiated,
            "serverInfo": {"name": "jaeger-mcp", "version": "0.1.0"},
            "capabilities": {"tools": {}},
        }))
        response.headers["Mcp-Session-Id"] = session_id
        return response

    if method == "tools/list":
        return JSONResponse(_mcp_result(request_id, {"tools": TOOLS}))

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            result = _call_tool(tool_name, arguments)
            return JSONResponse(_mcp_result(request_id, {
                "content": [{"type": "text", "text": json.dumps(result)}],
                "isError": False,
            }))
        except Exception as exc:
            return JSONResponse(_mcp_result(request_id, {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }))

    return JSONResponse(_mcp_error(request_id, -32601, f"Method not found: {method}"))


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
