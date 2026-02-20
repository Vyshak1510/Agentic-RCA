from __future__ import annotations

from datetime import datetime, timezone

from platform_core.models import AlertEnvelope
from platform_core.resolver import resolve_service_identity


def test_resolver_chain_prefers_nr_then_azure() -> None:
    alert = AlertEnvelope(
        source="newrelic",
        severity="high",
        incident_key="inc-100",
        entity_ids=["entity-a"],
        timestamps={"triggered_at": datetime.now(timezone.utc)},
        raw_payload={"env": "prod", "owner": "team-a"},
    )

    identity = resolve_service_identity(
        alert,
        nr_candidates=["svc-checkout"],
        azure_candidates=["svc-checkout-azure"],
        cmdb_candidates=["svc-checkout-cmdb"],
    )

    assert identity.canonical_service_id == "svc-checkout"
    assert identity.confidence > 0.6
    assert identity.ambiguous_candidates
