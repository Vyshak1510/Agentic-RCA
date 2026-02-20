from __future__ import annotations

from platform_core.models import AlertEnvelope, InvestigationPlan, PlanStep


def build_default_plan(investigation_id: str, alert: AlertEnvelope) -> InvestigationPlan:
    steps = [
        PlanStep(
            provider="newrelic" if alert.source == "newrelic" else "otel",
            rationale="Fetch error/latency signal around incident window",
            timeout_seconds=90,
            budget_weight=2,
            capability="metrics",
        ),
        PlanStep(
            provider="azure",
            rationale="Check recent infra events and resource health",
            timeout_seconds=120,
            budget_weight=2,
            capability="events",
        ),
        PlanStep(
            provider="otel",
            rationale="Collect traces/logs for impacted entities",
            timeout_seconds=180,
            budget_weight=3,
            capability="traces",
        ),
    ]
    return InvestigationPlan(
        investigation_id=investigation_id,
        ordered_steps=steps,
        max_api_calls=10,
        max_stage_wall_clock_seconds=600,
    )
