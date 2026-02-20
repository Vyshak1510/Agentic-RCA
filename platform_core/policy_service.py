from __future__ import annotations

from rca_plugin_sdk.interfaces import ConnectorPlugin


class PolicyService:
    def validate_connector(self, connector: ConnectorPlugin) -> None:
        if not connector.manifest.read_only:
            raise ValueError(f"Connector {connector.manifest.name} must be read-only in v1")

    def validate_redaction_state(self, redaction_state: str) -> None:
        if redaction_state != "redacted":
            raise ValueError("Evidence must be redacted before LLM synthesis")
