from __future__ import annotations

from datetime import datetime, timezone

import pytest

from platform_core.models import AlertEnvelope, Hypothesis, InvestigationPlan, PlanStep
from platform_core.planner import build_default_plan
from platform_core.policy import PolicyError, enforce_budget_policy, enforce_citation_policy


def test_default_plan_fits_budget() -> None:
    alert = AlertEnvelope(
        source="newrelic",
        severity="critical",
        incident_key="abc",
        entity_ids=["svc-a"],
        timestamps={"triggered_at": datetime.now(timezone.utc)},
        raw_payload={},
    )

    plan = build_default_plan("inv-1", alert)
    enforce_budget_policy(plan)
    assert len(plan.ordered_steps) <= plan.max_api_calls


def test_budget_policy_blocks_excessive_steps() -> None:
    plan = InvestigationPlan(
        investigation_id="inv-2",
        max_api_calls=1,
        max_stage_wall_clock_seconds=100,
        ordered_steps=[
            PlanStep(
                provider="otel",
                rationale="x",
                timeout_seconds=60,
                budget_weight=1,
                capability="traces",
            ),
            PlanStep(
                provider="azure",
                rationale="x",
                timeout_seconds=60,
                budget_weight=1,
                capability="events",
            ),
        ],
    )

    with pytest.raises(PolicyError):
        enforce_budget_policy(plan)


def test_citation_policy_requires_supporting_citations() -> None:
    hypotheses = [
        Hypothesis(statement="Cause A", confidence=0.9, supporting_citations=["c-1"]),
        Hypothesis(statement="Cause B", confidence=0.4, supporting_citations=[]),
    ]

    with pytest.raises(PolicyError):
        enforce_citation_policy(hypotheses)
