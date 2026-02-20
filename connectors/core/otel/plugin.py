from __future__ import annotations

from datetime import datetime, timezone

from rca_plugin_sdk.interfaces import ConnectorPlugin
from rca_plugin_sdk.manifest import ConnectorManifest


class OTelConnector(ConnectorPlugin):
    manifest = ConnectorManifest(
        name="otel-core",
        provider="otel",
        read_only=True,
        capabilities=["traces", "logs", "metrics"],
    )

    def discover_context(self, alert: dict, service_identity: dict) -> dict:
        return {
            "trace_ids": alert.get("trace_ids", []),
            "resource": alert.get("resource", {}),
            "service_identity": service_identity,
        }

    def collect_signals(self, plan_step: dict) -> list[dict]:
        return [
            {
                "provider": "otel",
                "type": plan_step.get("capability", "traces"),
                "value": "signal-sample",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]

    def normalize_evidence(self, raw_payload: dict) -> dict:
        return {
            "provider": "otel",
            "evidence_type": raw_payload.get("type", "trace"),
            "normalized_fields": raw_payload,
        }

    def healthcheck(self) -> dict[str, str]:
        return {"status": "ok", "provider": "otel"}


connector = OTelConnector()
