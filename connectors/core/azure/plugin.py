from __future__ import annotations

from datetime import datetime, timezone

from rca_plugin_sdk.interfaces import ConnectorPlugin
from rca_plugin_sdk.manifest import ConnectorManifest


class AzureConnector(ConnectorPlugin):
    manifest = ConnectorManifest(
        name="azure-core",
        provider="azure",
        read_only=True,
        capabilities=["resource_health", "events", "metrics"],
    )

    def discover_context(self, alert: dict, service_identity: dict) -> dict:
        return {
            "resource_ids": alert.get("entity_ids", []),
            "metadata": alert.get("resource_metadata", {}),
            "service_identity": service_identity,
        }

    def collect_signals(self, plan_step: dict) -> list[dict]:
        return [
            {
                "provider": "azure",
                "type": plan_step.get("capability", "events"),
                "value": "signal-sample",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]

    def normalize_evidence(self, raw_payload: dict) -> dict:
        return {
            "provider": "azure",
            "evidence_type": raw_payload.get("type", "event"),
            "normalized_fields": raw_payload,
        }

    def healthcheck(self) -> dict[str, str]:
        return {"status": "ok", "provider": "azure"}


connector = AzureConnector()
