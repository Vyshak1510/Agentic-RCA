from __future__ import annotations

from platform_core.models import AlertEnvelope, InvestigationPlan, PlanStep


def build_default_plan(investigation_id: str, alert: AlertEnvelope) -> InvestigationPlan:
    steps: list[PlanStep] = []

    if alert.source == "newrelic":
        steps.append(
            PlanStep(
                provider="newrelic",
                rationale="Fetch New Relic service metrics around incident window",
                timeout_seconds=90,
                budget_weight=2,
                capability="metrics",
            )
        )
        steps.append(
            PlanStep(
                provider="newrelic",
                rationale="Check New Relic events/change signals for the incident scope",
                timeout_seconds=120,
                budget_weight=2,
                capability="events",
            )
        )
    elif alert.source == "azure":
        steps.append(
            PlanStep(
                provider="azure",
                rationale="Check Azure resource-health/events near the incident window",
                timeout_seconds=120,
                budget_weight=2,
                capability="events",
            )
        )
        steps.append(
            PlanStep(
                provider="azure",
                rationale="Collect Azure metrics for impacted resources",
                timeout_seconds=120,
                budget_weight=2,
                capability="metrics",
            )
        )
    else:
        steps.append(
            PlanStep(
                provider="otel",
                rationale="Fetch OTel metrics around incident window",
                timeout_seconds=90,
                budget_weight=2,
                capability="metrics",
            )
        )

    steps.append(
        PlanStep(
            provider="otel",
            rationale="Collect traces/logs for impacted entities",
            timeout_seconds=180,
            budget_weight=3,
            capability="traces",
        )
    )

    return InvestigationPlan(
        investigation_id=investigation_id,
        ordered_steps=steps,
        max_api_calls=10,
        max_stage_wall_clock_seconds=600,
    )
