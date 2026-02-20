from __future__ import annotations

from typing import Any

from temporalio import activity

from services.orchestrator.app.pipeline import (
    build_plan_stage,
    collect_evidence_stage,
    emit_eval_event_stage,
    publish_stage,
    resolve_service_stage,
    synthesize_report_stage,
)


@activity.defn(name="resolve-service-identity")
async def resolve_service_activity(alert_payload: dict[str, Any]) -> dict[str, Any]:
    return resolve_service_stage(alert_payload)


@activity.defn(name="build-investigation-plan")
async def build_plan_activity(investigation_id: str, alert_payload: dict[str, Any]) -> dict[str, Any]:
    return build_plan_stage(investigation_id, alert_payload)


@activity.defn(name="collect-evidence")
async def collect_evidence_activity(
    investigation_id: str,
    alert_payload: dict[str, Any],
    plan_payload: dict[str, Any],
) -> dict[str, Any]:
    return collect_evidence_stage(investigation_id, alert_payload, plan_payload)


@activity.defn(name="synthesize-rca-report")
async def synthesize_report_activity(
    alert_payload: dict[str, Any],
    service_identity_payload: dict[str, Any],
    evidence_payload: list[dict[str, Any]],
) -> dict[str, Any]:
    return synthesize_report_stage(alert_payload, service_identity_payload, evidence_payload)


@activity.defn(name="publish-report")
async def publish_activity(
    alert_payload: dict[str, Any],
    report_payload: dict[str, Any],
    enabled: bool = True,
) -> dict[str, Any]:
    return publish_stage(alert_payload, report_payload, enabled)


@activity.defn(name="emit-eval-event")
async def emit_eval_event_activity(
    investigation_id: str,
    report_payload: dict[str, Any],
    evidence_payload: list[dict[str, Any]],
    latency_seconds: float,
) -> dict[str, Any]:
    return emit_eval_event_stage(investigation_id, report_payload, evidence_payload, latency_seconds)
