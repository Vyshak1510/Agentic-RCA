from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tests.helpers import load_module


def test_end_to_end_ingest_and_eval() -> None:
    ingest_module = load_module("services/ingest-api/app/main.py", "ingest_api_main")
    eval_module = load_module("services/eval-service/app/main.py", "eval_service_main")

    ingest_client = TestClient(ingest_module.app)
    eval_client = TestClient(eval_module.app)

    alert = {
        "source": "newrelic",
        "severity": "critical",
        "incident_key": "nr-555",
        "entity_ids": ["svc-checkout"],
        "timestamps": {"triggered_at": datetime.now(timezone.utc).isoformat()},
        "raw_payload_ref": "newrelic://nr-555",
        "raw_payload": {"condition": "error_rate"},
    }

    ingest_resp = ingest_client.post("/v1/alerts", json=alert)
    assert ingest_resp.status_code == 200
    inv_id = ingest_resp.json()["investigation_id"]

    inv_resp = ingest_client.get(f"/v1/investigations/{inv_id}")
    assert inv_resp.status_code == 200
    assert inv_resp.json()["id"] == inv_id

    eval_resp = eval_client.post("/v1/evals/runs", json={"dataset_path": "evals/golden-datasets/sample.json"})
    assert eval_resp.status_code == 200
    assert "gate_passed" in eval_resp.json()
