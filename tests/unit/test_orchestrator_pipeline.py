from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from services.orchestrator.app.pipeline import (
    build_plan_stage,
    collect_evidence_stage,
    emit_eval_event_stage,
    publish_stage,
    resolve_service_stage,
    synthesize_report_stage,
)


def _sample_alert() -> dict:
    return {
        "source": "newrelic",
        "severity": "critical",
        "incident_key": f"nr-{uuid4()}",
        "entity_ids": ["service-checkout"],
        "timestamps": {"triggered_at": datetime.now(timezone.utc).isoformat()},
        "raw_payload_ref": "newrelic://sample",
        "raw_payload": {"owner": "payments-team", "env": "prod"},
    }


def test_pipeline_generates_rca_payload() -> None:
    investigation_id = f"inv-{uuid4()}"
    alert = _sample_alert()

    service_identity = resolve_service_stage(alert)
    assert service_identity["canonical_service_id"]

    plan = build_plan_stage(investigation_id, alert)
    assert len(plan["ordered_steps"]) > 0

    evidence_result = collect_evidence_stage(investigation_id, alert, plan)
    assert evidence_result["evidence"]

    synthesis = synthesize_report_stage(alert, service_identity, evidence_result["evidence"])
    report = synthesis["report"]
    assert len(report["top_hypotheses"]) <= 3
    for hypothesis in report["top_hypotheses"]:
        assert hypothesis["supporting_citations"]

    publish_result = publish_stage(alert, report, enabled=False)
    assert publish_result["published"] is False

    eval_event = emit_eval_event_stage(investigation_id, report, evidence_result["evidence"], latency_seconds=42.0)
    assert eval_event["requires_human_review"] is True
    assert eval_event["rollout_mode"] == "shadow"
