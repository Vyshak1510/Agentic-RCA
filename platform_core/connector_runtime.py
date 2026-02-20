from __future__ import annotations

from typing import Iterable

from rca_plugin_sdk.interfaces import ConnectorPlugin


class ConnectorRuntime:
    def __init__(self, connectors: Iterable[ConnectorPlugin]) -> None:
        self._connectors = {connector.manifest.provider: connector for connector in connectors}

    def route_collect(self, provider: str, capability: str, plan_step: dict) -> list[dict]:
        connector = self._connectors.get(provider)
        if not connector:
            return []
        if capability not in connector.manifest.capabilities:
            return []
        return connector.collect_signals(plan_step)

    def health(self) -> dict[str, dict[str, str]]:
        return {provider: connector.healthcheck() for provider, connector in self._connectors.items()}
