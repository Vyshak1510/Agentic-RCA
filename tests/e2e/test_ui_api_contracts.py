from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tests.helpers import load_module


def _ingest_alert(client: TestClient, incident_key: str, severity: str = "critical") -> dict:
    alert = {
        "source": "newrelic",
        "severity": severity,
        "incident_key": incident_key,
        "entity_ids": ["svc-checkout"],
        "timestamps": {"triggered_at": datetime.now(timezone.utc).isoformat()},
        "raw_payload_ref": f"newrelic://{incident_key}",
        "raw_payload": {"condition": "error_rate"},
    }
    response = client.post("/v1/alerts", json=alert)
    assert response.status_code == 200
    return response.json()


def test_list_investigations_for_ui() -> None:
    ingest_module = load_module("services/ingest-api/app/main.py", "ingest_ui_api")
    client = TestClient(ingest_module.app)

    _ingest_alert(client, "ui-1", severity="critical")
    _ingest_alert(client, "ui-2", severity="high")

    response = client.get("/v1/investigations?page=1&page_size=10&severity=critical")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 1
    assert payload["items"]


def test_run_apis_for_workflow_mapper() -> None:
    ingest_module = load_module("services/ingest-api/app/main.py", "ingest_ui_runs")
    client = TestClient(ingest_module.app)

    created = _ingest_alert(client, "ui-runs", severity="critical")
    investigation_id = created["investigation_id"]
    run_id = created["run_id"]
    assert investigation_id
    assert run_id

    list_runs = client.get(f"/v1/investigations/{investigation_id}/runs")
    assert list_runs.status_code == 200
    list_payload = list_runs.json()
    assert list_payload["items"]

    run_detail = client.get(f"/v1/investigations/{investigation_id}/runs/{run_id}")
    assert run_detail.status_code == 200
    assert run_detail.json()["run_id"] == run_id

    manual_run = client.post(f"/v1/investigations/{investigation_id}/runs", json={"publish_outputs": False})
    assert manual_run.status_code == 200
    assert manual_run.json()["run_id"]

    event_payload = {
        "run_id": run_id,
        "investigation_id": investigation_id,
        "workflow_id": created.get("workflow_id"),
        "stage_id": "resolve_service_identity",
        "stage_status": "running",
        "run_status": "running",
        "attempt": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": "resolve_service_identity started",
        "citations": [],
        "metadata": {"source": "contract-test"},
        "logs": [],
    }
    push_event = client.post("/v1/internal/runs/events", json=event_payload)
    assert push_event.status_code == 200
    assert push_event.json()["status"] == "ok"


def test_settings_connector_and_llm_routes() -> None:
    ingest_module = load_module("services/ingest-api/app/main.py", "ingest_ui_settings")
    client = TestClient(ingest_module.app)
    headers = {"x-user-role": "admin", "x-tenant-id": "default", "x-user-id": "ops-admin"}

    connector_payload = {
        "tenant": "default",
        "environment": "prod",
        "mode": "raw_key",
        "raw_key": "nr_test_123456",
    }
    upsert = client.put("/v1/settings/connectors/newrelic", json=connector_payload, headers=headers)
    assert upsert.status_code == 200
    assert upsert.json()["key_last4"] == "3456"

    settings = client.get("/v1/settings/connectors?environment=prod", headers=headers)
    assert settings.status_code == 200
    assert settings.json()["items"]

    llm_payload = {
        "tenant": "default",
        "environment": "prod",
        "primary_model": "codex",
        "fallback_model": "claude",
        "key_ref": "llm-provider-secret",
    }
    llm_upsert = client.put("/v1/settings/llm-routes", json=llm_payload, headers=headers)
    assert llm_upsert.status_code == 200

    llm_list = client.get("/v1/settings/llm-routes?environment=prod", headers=headers)
    assert llm_list.status_code == 200
    assert llm_list.json()["items"][0]["primary_model"] == "codex"


def test_settings_mcp_prompt_rollout_and_layout_apis() -> None:
    ingest_module = load_module("services/ingest-api/app/main.py", "ingest_ui_agentic")
    client = TestClient(ingest_module.app)
    admin_headers = {"x-user-role": "admin", "x-tenant-id": "default", "x-user-id": "ops-admin"}
    user_headers = {"x-user-role": "member", "x-tenant-id": "default", "x-user-id": "ops-user"}

    mcp_payload = {
        "tenant": "default",
        "environment": "prod",
        "transport": "http_sse",
        "base_url": "http://localhost:9998",
        "secret_ref_name": "mcp-secret",
        "secret_ref_key": "token",
        "timeout_seconds": 2,
        "enabled": True,
    }
    mcp_upsert = client.put("/v1/settings/mcp-servers/mock-mcp", json=mcp_payload, headers=admin_headers)
    assert mcp_upsert.status_code == 200
    assert mcp_upsert.json()["server_id"] == "mock-mcp"

    mcp_list = client.get("/v1/settings/mcp-servers?environment=prod", headers=user_headers)
    assert mcp_list.status_code == 200
    assert mcp_list.json()["items"]

    mcp_test = client.post("/v1/settings/mcp-servers/mock-mcp/test?environment=prod", json={}, headers=user_headers)
    assert mcp_test.status_code == 200
    assert "success" in mcp_test.json()

    mcp_tools = client.get("/v1/settings/mcp-servers/mock-mcp/tools?environment=prod", headers=user_headers)
    assert mcp_tools.status_code == 200
    assert "items" in mcp_tools.json()

    prompt_payload = {
        "tenant": "default",
        "environment": "prod",
        "system_prompt": "Resolver agent prompt.",
        "objective_template": "Resolve {{incident_key}}.",
        "max_turns": 4,
        "max_tool_calls": 6,
        "tool_allowlist": ["context.alert_entities"],
    }
    prompt_upsert = client.put(
        "/v1/settings/agent-prompts/resolve_service_identity",
        json=prompt_payload,
        headers=admin_headers,
    )
    assert prompt_upsert.status_code == 200
    assert prompt_upsert.json()["stage_id"] == "resolve_service_identity"

    prompt_list = client.get("/v1/settings/agent-prompts?environment=prod", headers=user_headers)
    assert prompt_list.status_code == 200
    assert prompt_list.json()["items"]

    rollout_upsert = client.put(
        "/v1/settings/agent-rollout",
        json={"tenant": "default", "environment": "prod", "mode": "compare"},
        headers=admin_headers,
    )
    assert rollout_upsert.status_code == 200
    assert rollout_upsert.json()["mode"] == "compare"

    rollout_get = client.get("/v1/settings/agent-rollout?environment=prod", headers=user_headers)
    assert rollout_get.status_code == 200
    assert rollout_get.json()["mode"] in {"compare", "active"}

    layout_get_missing = client.get("/v1/ui/workflow-layouts/rca-v1-stage-map", headers=user_headers)
    assert layout_get_missing.status_code == 200
    assert layout_get_missing.json()["status"] == "not_found"

    layout_payload = {
        "nodes": [
            {"id": "resolve_service_identity", "x": 100, "y": 80},
            {"id": "build_investigation_plan", "x": 430, "y": 100},
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1.1},
    }
    layout_upsert = client.put("/v1/ui/workflow-layouts/rca-v1-stage-map", json=layout_payload, headers=user_headers)
    assert layout_upsert.status_code == 200
    assert layout_upsert.json()["workflow_key"] == "rca-v1-stage-map"

    layout_get = client.get("/v1/ui/workflow-layouts/rca-v1-stage-map", headers=user_headers)
    assert layout_get.status_code == 200
    assert layout_get.json()["nodes"]
