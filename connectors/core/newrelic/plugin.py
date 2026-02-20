from __future__ import annotations

from datetime import datetime, timezone

from rca_plugin_sdk.interfaces import ConnectorPlugin
from rca_plugin_sdk.manifest import ConnectorManifest


class NewRelicConnector(ConnectorPlugin):
    manifest = ConnectorManifest(
        name="newrelic-core",
        provider="newrelic",
        read_only=True,
        capabilities=["metrics", "events", "entities"],
    )

    def discover_context(self, alert: dict, service_identity: dict) -> dict:
        return {
            "entity_ids": alert.get("entity_ids", []),
            "tags": alert.get("tags", {}),
            "service_identity": service_identity,
        }

    def collect_signals(self, plan_step: dict) -> list[dict]:
        return [
            {
                "provider": "newrelic",
                "type": plan_step.get("capability", "metrics"),
                "value": "signal-sample",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]

    def normalize_evidence(self, raw_payload: dict) -> dict:
        return {
            "provider": "newrelic",
            "evidence_type": raw_payload.get("type", "metric"),
            "normalized_fields": raw_payload,
        }

    def healthcheck(self) -> dict[str, str]:
        return {"status": "ok", "provider": "newrelic"}


connector = NewRelicConnector()
