from __future__ import annotations

import time
from datetime import datetime, timezone

from platform_core.models import AlertEnvelope
from platform_core.planner import build_default_plan


def test_plan_construction_is_fast() -> None:
    alert = AlertEnvelope(
        source="otel",
        severity="high",
        incident_key="perf-1",
        entity_ids=["svc-a"],
        timestamps={"triggered_at": datetime.now(timezone.utc)},
        raw_payload={},
    )

    start = time.perf_counter()
    for _ in range(1000):
        build_default_plan("inv-perf", alert)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 500
