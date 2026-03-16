"""Prometheus MCP server exposing a small read-only query surface over HTTP."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

PROMETHEUS_BASE_URL = os.environ.get("PROMETHEUS_BASE_URL", "http://localhost:9090").rstrip("/")
TIMEOUT = int(os.environ.get("PROMETHEUS_TIMEOUT_SECONDS", "10"))
PROTOCOL_VERSION = "2025-06-18"

app = FastAPI(title="prometheus-mcp")

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_label_names",
        "description": "List all Prometheus label names.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "annotations": {"readOnlyHint": True, "lightProbeHint": True},
    },
    {
        "name": "list_label_values",
        "description": "List Prometheus label values for a given label name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Prometheus label name"},
            },
            "required": ["label"],
        },
        "annotations": {"readOnlyHint": True, "lightProbeHint": True},
    },
    {
        "name": "query_instant",
        "description": "Run an instant Prometheus query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "PromQL query"},
                "time": {"type": "string", "description": "RFC3339 timestamp or unix seconds"},
            },
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "query_range",
        "description": "Run a range Prometheus query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "PromQL query"},
                "start": {"type": "string", "description": "RFC3339 timestamp or unix seconds"},
                "end": {"type": "string", "description": "RFC3339 timestamp or unix seconds"},
                "step": {"type": "string", "description": "Prometheus step value, e.g. 60s"},
            },
            "required": ["query", "start", "end"],
        },
        "annotations": {"readOnlyHint": True},
    },
]


def _prom_get(path: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{PROMETHEUS_BASE_URL}{path}"
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def _to_prom_time(value: Any) -> str:
    if isinstance(value, (int, float)):
        return str(value)
    if not isinstance(value, str):
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    text = value.strip()
    if not text:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if text.isdigit():
        return text
    return text.replace("+00:00", "Z")


def _summarize_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    data = result.get("data") if isinstance(result, dict) else {}
    entries = data.get("result") if isinstance(data, dict) and isinstance(data.get("result"), list) else []
    summary: list[dict[str, Any]] = []
    for item in entries[:12]:
        if not isinstance(item, dict):
            continue
        metric = item.get("metric") if isinstance(item.get("metric"), dict) else {}
        values = item.get("values") if isinstance(item.get("values"), list) else []
        value = item.get("value") if isinstance(item.get("value"), list) else []
        summary.append(
            {
                "metric": metric,
                "value": value,
                "samples": len(values),
                "values": values[:10],
            }
        )
    return summary


def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "list_label_names":
        data = _prom_get("/api/v1/labels")
        return data.get("data", [])

    if name == "list_label_values":
        label = arguments["label"]
        data = _prom_get(f"/api/v1/label/{label}/values")
        return data.get("data", [])

    if name == "query_instant":
        params = {"query": arguments["query"]}
        if "time" in arguments:
            params["time"] = _to_prom_time(arguments["time"])
        data = _prom_get("/api/v1/query", params)
        return _summarize_result(data)

    if name == "query_range":
        params = {
            "query": arguments["query"],
            "start": _to_prom_time(arguments["start"]),
            "end": _to_prom_time(arguments["end"]),
            "step": arguments.get("step", "60s"),
        }
        data = _prom_get("/api/v1/query_range", params)
        return _summarize_result(data)

    return {"error": f"unknown tool: {name}"}


def _mcp_result(request_id: str, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _mcp_error(request_id: str | None, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/")
@app.post("/mcp")
async def mcp_endpoint(body: dict[str, Any]) -> JSONResponse:
    method = body.get("method")
    request_id = body.get("id")

    if method == "initialize":
        return JSONResponse(
            _mcp_result(
                str(request_id or f"init-{uuid4().hex}"),
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
                    "serverInfo": {"name": "prometheus-mcp", "version": "0.1.0"},
                },
            ),
            headers={"Mcp-Session-Id": str(uuid4())},
        )

    if method == "notifications/initialized":
        return JSONResponse({})

    if method == "tools/list":
        return JSONResponse(_mcp_result(str(request_id or f"tools-{uuid4().hex}"), {"tools": TOOLS}))

    if method == "tools/call":
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        name = str(params.get("name") or "").strip()
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if not name:
            return JSONResponse(_mcp_error(request_id, -32602, "tool name is required"), status_code=400)
        try:
            result = _call_tool(name, arguments)
        except Exception as exc:
            return JSONResponse(
                _mcp_result(
                    str(request_id or f"err-{uuid4().hex}"),
                    {"isError": True, "content": [{"type": "text", "text": str(exc)}]},
                ),
                status_code=200,
            )
        return JSONResponse(_mcp_result(str(request_id or f"call-{uuid4().hex}"), result))

    return JSONResponse(_mcp_error(request_id, -32601, f"unknown method {method}"), status_code=404)
