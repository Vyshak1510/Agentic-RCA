from __future__ import annotations

from connectors.core.azure.plugin import AzureConnector
from connectors.core.newrelic.plugin import NewRelicConnector
from connectors.core.otel.plugin import OTelConnector


def test_connector_signal_collection_and_normalization() -> None:
    connectors = [NewRelicConnector(), AzureConnector(), OTelConnector()]

    for connector in connectors:
        signals = connector.collect_signals({"capability": connector.manifest.capabilities[0]})
        assert signals
        evidence = connector.normalize_evidence(signals[0])
        assert evidence["provider"] == connector.manifest.provider
        assert "normalized_fields" in evidence
