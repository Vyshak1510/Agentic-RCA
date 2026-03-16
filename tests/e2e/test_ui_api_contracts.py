from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tests.helpers import load_module


def _auth_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    token = os.getenv("API_KEY")
    if token:
        headers["x-api-key"] = token
    return headers


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
    response = client.post("/v1/alerts", json=alert, headers=_auth_headers())
    assert response.status_code == 200
    return response.json()


def test_list_investigations_for_ui() -> None:
    ingest_module = load_module("services/ingest-api/app/main.py", "ingest_ui_api")
    client = TestClient(ingest_module.app)

    _ingest_alert(client, "ui-1", severity="critical")
    _ingest_alert(client, "ui-2", severity="high")

    response = client.get("/v1/investigations?page=1&page_size=10&severity=critical", headers=_auth_headers())
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

    list_runs = client.get(f"/v1/investigations/{investigation_id}/runs", headers=_auth_headers())
    assert list_runs.status_code == 200
    list_payload = list_runs.json()
    assert list_payload["items"]

    run_detail = client.get(f"/v1/investigations/{investigation_id}/runs/{run_id}", headers=_auth_headers())
    assert run_detail.status_code == 200
    assert run_detail.json()["run_id"] == run_id

    manual_run = client.post(
        f"/v1/investigations/{investigation_id}/runs",
        json={"publish_outputs": False},
        headers=_auth_headers(),
    )
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


def test_investigation_record_persists_stage_outputs_from_events() -> None:
    ingest_module = load_module("services/ingest-api/app/main.py", "ingest_ui_stage_persist")
    client = TestClient(ingest_module.app)

    created = _ingest_alert(client, "ui-stage-persist", severity="critical")
    investigation_id = created["investigation_id"]
    run_id = created["run_id"]
    workflow_id = created.get("workflow_id")
    timestamp = datetime.now(timezone.utc).isoformat()

    service_identity = {
        "canonical_service_id": "svc-frontend-proxy",
        "owner": "platform-team",
        "env": "prod",
        "dependency_graph_refs": ["dep-a", "dep-b"],
        "mapped_provider_ids": {"grafana": "frontend-proxy"},
        "confidence": 0.82,
        "ambiguous_candidates": [],
    }
    plan = {
        "investigation_id": investigation_id,
        "ordered_steps": [
            {
                "provider": "otel",
                "rationale": "Fetch trace/metrics around incident window",
                "timeout_seconds": 90,
                "budget_weight": 2,
                "capability": "traces",
            }
        ],
        "max_api_calls": 10,
        "max_stage_wall_clock_seconds": 600,
    }
    evidence = [
        {
            "provider": "otel",
            "timestamp": timestamp,
            "evidence_type": "traces",
            "normalized_fields": {"span_error_rate": 0.31},
            "citation_id": "c-otel-1",
            "redaction_state": "redacted",
        }
    ]
    hypotheses = [
        {
            "statement": "Dependency latency caused upstream timeout amplification.",
            "confidence": 0.74,
            "supporting_citations": ["c-otel-1"],
            "counter_evidence_citations": [],
        }
    ]
    report = {
        "top_hypotheses": hypotheses,
        "likely_cause": hypotheses[0]["statement"],
        "blast_radius": "Frontend and checkout request paths.",
        "recommended_manual_actions": ["Rollback recent ingress change and monitor latency."],
        "confidence": 0.74,
    }

    events = [
        {
            "stage_id": "resolve_service_identity",
            "metadata": {"service_identity": service_identity},
        },
        {
            "stage_id": "build_investigation_plan",
            "metadata": {"plan": plan},
        },
        {
            "stage_id": "collect_evidence",
            "metadata": {"evidence": evidence},
        },
        {
            "stage_id": "synthesize_rca_report",
            "metadata": {"report": report, "hypotheses": hypotheses},
        },
    ]

    for idx, item in enumerate(events, start=1):
        response = client.post(
            "/v1/internal/runs/events",
            json={
                "run_id": run_id,
                "investigation_id": investigation_id,
                "workflow_id": workflow_id,
                "stage_id": item["stage_id"],
                "stage_status": "completed",
                "run_status": "running",
                "attempt": 1,
                "timestamp": timestamp,
                "message": f"{item['stage_id']} completed",
                "citations": [],
                "metadata": item["metadata"],
                "logs": [],
                "event_index": idx,
            },
        )
        assert response.status_code == 200

    investigation = client.get(f"/v1/investigations/{investigation_id}", headers=_auth_headers())
    assert investigation.status_code == 200
    payload = investigation.json()
    assert payload["service_identity"]["canonical_service_id"] == "svc-frontend-proxy"
    assert payload["plan"]["ordered_steps"][0]["provider"] == "otel"
    assert payload["evidence"][0]["citation_id"] == "c-otel-1"
    assert payload["hypotheses"][0]["statement"].startswith("Dependency latency")
    assert payload["report"]["likely_cause"].startswith("Dependency latency")


def test_settings_connector_and_llm_routes(monkeypatch) -> None:
    monkeypatch.setenv("RCA_MODEL_ALIAS_CODEX", "openai/mock-primary")
    monkeypatch.setenv("RCA_MODEL_ALIAS_CLAUDE", "anthropic/mock-fallback")
    ingest_module = load_module("services/ingest-api/app/main.py", "ingest_ui_settings")
    client = TestClient(ingest_module.app)
    headers = _auth_headers({"x-user-role": "admin", "x-tenant-id": "default", "x-user-id": "ops-admin"})

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


def test_settings_llm_routes_reject_unresolved_alias(monkeypatch) -> None:
    monkeypatch.delenv("RCA_MODEL_ALIAS_CODEX", raising=False)
    monkeypatch.delenv("RCA_MODEL_ALIAS_CLAUDE", raising=False)

    ingest_module = load_module("services/ingest-api/app/main.py", "ingest_ui_settings_llm_invalid")
    client = TestClient(ingest_module.app)
    headers = _auth_headers({"x-user-role": "admin", "x-tenant-id": "default", "x-user-id": "ops-admin"})

    llm_payload = {
        "tenant": "default",
        "environment": "prod",
        "primary_model": "codex",
        "fallback_model": "claude",
        "key_ref": "llm-provider-secret",
    }
    llm_upsert = client.put("/v1/settings/llm-routes", json=llm_payload, headers=headers)
    assert llm_upsert.status_code == 400
    assert "invalid model route" in llm_upsert.json()["detail"]


def test_settings_mcp_prompt_rollout_and_layout_apis() -> None:
    ingest_module = load_module("services/ingest-api/app/main.py", "ingest_ui_agentic")
    client = TestClient(ingest_module.app)
    admin_headers = _auth_headers({"x-user-role": "admin", "x-tenant-id": "default", "x-user-id": "ops-admin"})
    user_headers = _auth_headers({"x-user-role": "member", "x-tenant-id": "default", "x-user-id": "ops-user"})

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

    team_payload = {
        "tenant": "default",
        "environment": "prod",
        "enabled": True,
        "objective_prompt": "Investigate application-level failures using traces and logs.",
        "tool_allowlist": ["mcp.jaeger.*"],
        "max_tool_calls": 6,
        "max_parallel_calls": 3,
        "timeout_seconds": 30,
    }
    team_upsert = client.put("/v1/settings/investigation-teams/app", json=team_payload, headers=admin_headers)
    assert team_upsert.status_code == 200
    assert team_upsert.json()["team_id"] == "app"

    team_list = client.get("/v1/settings/investigation-teams?environment=prod", headers=user_headers)
    assert team_list.status_code == 200
    assert team_list.json()["items"]

    stage_mission_get = client.get(
        "/v1/settings/stage-missions/resolve_service_identity?environment=prod",
        headers=user_headers,
    )
    assert stage_mission_get.status_code == 200
    assert stage_mission_get.json()["stage_id"] == "resolve_service_identity"

    stage_mission_upsert = client.put(
        "/v1/settings/stage-missions/resolve_service_identity",
        json={
            "tenant": "default",
            "environment": "prod",
            "mission_objective": "Resolve service identity with explicit ambiguity handling.",
            "required_checks": ["alert_entities_reviewed", "canonical_service_selected"],
            "allowed_tools": ["mcp.jaeger.*", "mcp.grafana.*"],
            "completion_criteria": ["confidence_reported"],
            "unknown_not_available_rules": ["missing_entity_context"],
            "relevance_weights": {"alert": 1.0, "context_pack": 0.6},
        },
        headers=admin_headers,
    )
    assert stage_mission_upsert.status_code == 200

    team_mission_get = client.get("/v1/settings/team-missions/app?environment=prod", headers=user_headers)
    assert team_mission_get.status_code == 200
    assert team_mission_get.json()["team_id"] == "app"

    team_mission_upsert = client.put(
        "/v1/settings/team-missions/app",
        json={
            "tenant": "default",
            "environment": "prod",
            "mission_objective": "Application mission policy",
            "required_checks": ["trace_errors_checked"],
            "allowed_tools": ["mcp.jaeger.*"],
            "completion_criteria": ["mini_rca_produced"],
            "unknown_not_available_rules": ["no_trace_evidence"],
            "relevance_weights": {"service_scoped": 1.0, "global": 0.4},
        },
        headers=admin_headers,
    )
    assert team_mission_upsert.status_code == 200

    context_pack_create = client.post(
        "/v1/settings/context-packs",
        json={
            "tenant": "default",
            "environment": "prod",
            "pack_id": "otel-demo",
            "name": "OTel Demo Context",
            "description": "context for recommendation incident",
            "stage_bindings": ["resolve_service_identity", "collect_evidence"],
            "team_bindings": ["app", "infra"],
            "service_tags": ["recommendationservice"],
            "infra_components": ["redis", "kubernetes"],
            "dependencies": ["frontend", "checkout"],
        },
        headers=admin_headers,
    )
    assert context_pack_create.status_code == 200
    assert context_pack_create.json()["pack_id"] == "otel-demo"

    context_artifact_upload = client.post(
        "/v1/settings/context-packs/otel-demo/artifacts",
        json={
            "tenant": "default",
            "environment": "prod",
            "filename": "runbook.md",
            "artifact_type": "markdown",
            "content": "# Recommendation\nInvestigate recommendationservice latency and cache.",
            "operator_notes": "updated today",
            "metadata": {"source": "ops"},
        },
        headers=admin_headers,
    )
    assert context_artifact_upload.status_code == 200
    assert context_artifact_upload.json()["version"] >= 2

    context_activate = client.post(
        "/v1/settings/context-packs/otel-demo/activate",
        json={"tenant": "default", "environment": "prod"},
        headers=admin_headers,
    )
    assert context_activate.status_code == 200
    assert context_activate.json()["active"] is True

    context_list = client.get("/v1/settings/context-packs?environment=prod", headers=user_headers)
    assert context_list.status_code == 200
    assert context_list.json()["items"]
    assert context_list.json()["active"]["pack_id"] == "otel-demo"

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


def test_grafana_webhook_convenience_ingest() -> None:
    ingest_module = load_module("services/ingest-api/app/main.py", "ingest_grafana_webhook")
    client = TestClient(ingest_module.app)

    payload = {
        "receiver": "rca-webhook",
        "status": "firing",
        "groupKey": "{}:{alertname=\"HighErrorRate\",service=\"checkout\"}",
        "externalURL": "http://grafana.local/alerting/list",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighErrorRate",
                    "service": "checkout",
                    "instance": "checkout-0",
                    "severity": "critical",
                },
                "annotations": {"summary": "checkout error rate high"},
                "startsAt": "2026-03-06T12:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "fingerprint": "abc123",
                "generatorURL": "http://grafana.local/d/xyz",
            }
        ],
    }
    response = client.post("/v1/alerts/grafana", json=payload, headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["investigation_id"]
    assert body["run_id"]

    inv = client.get(f"/v1/investigations/{body['investigation_id']}", headers=_auth_headers())
    assert inv.status_code == 200
    alert = inv.json()["alert"]
    assert alert["source"] == "grafana"
    assert alert["severity"] == "critical"
    assert "checkout" in alert["entity_ids"]
