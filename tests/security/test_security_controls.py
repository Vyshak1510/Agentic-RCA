from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from platform_core.redaction import redact_payload
from tests.helpers import load_module


def test_redaction_prevents_pii_leakage() -> None:
    payload = {"message": "email admin@company.com and ssn 111-22-3333"}
    redacted = redact_payload(payload)
    assert "company.com" not in redacted["message"]
    assert "111-22-3333" not in redacted["message"]


def test_api_key_auth_bypass_prevented(monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "secret")
    ingest_module = load_module("services/ingest-api/app/main.py", "ingest_api_main_auth")
    client = TestClient(ingest_module.app)

    alert = {
        "source": "newrelic",
        "severity": "critical",
        "incident_key": "auth-1",
        "entity_ids": ["svc-auth"],
        "timestamps": {"triggered_at": datetime.now(timezone.utc).isoformat()},
        "raw_payload_ref": "newrelic://auth-1",
        "raw_payload": {},
    }

    denied = client.post("/v1/alerts", json=alert)
    assert denied.status_code == 401

    allowed = client.post("/v1/alerts", json=alert, headers={"x-api-key": "secret"})
    assert allowed.status_code == 200
