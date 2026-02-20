from __future__ import annotations

import pytest

from connectors.core.newrelic.plugin import NewRelicConnector
from platform_core.connector_runtime import ConnectorRuntime
from platform_core.policy_service import PolicyService


def test_connector_runtime_routes_by_capability() -> None:
    connector = NewRelicConnector()
    runtime = ConnectorRuntime([connector])

    signals = runtime.route_collect("newrelic", "metrics", {"capability": "metrics"})
    blocked = runtime.route_collect("newrelic", "traces", {"capability": "traces"})

    assert signals
    assert blocked == []


def test_policy_service_enforces_read_only() -> None:
    policy = PolicyService()
    connector = NewRelicConnector()
    policy.validate_connector(connector)

    connector.manifest.read_only = False
    with pytest.raises(ValueError):
        policy.validate_connector(connector)
