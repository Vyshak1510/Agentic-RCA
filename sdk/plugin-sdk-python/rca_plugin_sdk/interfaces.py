from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from rca_plugin_sdk.manifest import ConnectorManifest


class ConnectorPlugin(ABC):
    manifest: ConnectorManifest

    @abstractmethod
    def discover_context(self, alert: dict[str, Any], service_identity: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def collect_signals(self, plan_step: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def normalize_evidence(self, raw_payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def healthcheck(self) -> dict[str, str]:
        raise NotImplementedError
