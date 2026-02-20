from __future__ import annotations

from connectors.core.azure.plugin import AzureConnector
from connectors.core.newrelic.plugin import NewRelicConnector
from connectors.core.otel.plugin import OTelConnector
from rca_plugin_sdk.interfaces import ConnectorPlugin


def test_connectors_implement_contract() -> None:
    connectors = [NewRelicConnector(), AzureConnector(), OTelConnector()]

    for connector in connectors:
        assert isinstance(connector, ConnectorPlugin)
        assert connector.manifest.read_only is True
        assert connector.manifest.capabilities
        assert connector.healthcheck()["status"] == "ok"
