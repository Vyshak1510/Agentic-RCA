from __future__ import annotations

from platform_core.models import Hypothesis, InvestigationPlan


class PolicyError(ValueError):
    pass


def enforce_citation_policy(hypotheses: list[Hypothesis]) -> None:
    for idx, hypothesis in enumerate(hypotheses, start=1):
        if not hypothesis.supporting_citations:
            raise PolicyError(f"Hypothesis {idx} is missing supporting citations.")


def enforce_budget_policy(plan: InvestigationPlan) -> None:
    if len(plan.ordered_steps) > plan.max_api_calls:
        raise PolicyError("Investigation plan exceeds max API call budget.")

    total_timeout = sum(step.timeout_seconds for step in plan.ordered_steps)
    if total_timeout > plan.max_stage_wall_clock_seconds:
        raise PolicyError("Investigation plan exceeds stage wall-clock budget.")
