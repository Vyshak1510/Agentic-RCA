from __future__ import annotations

from datetime import datetime, timezone

import pytest

from platform_core.agent_runtime import run_resolver_agent
from platform_core.llm_router import ModelRoute
from platform_core.models import AgentPromptProfile, AgentToolTrace, McpToolDescriptor, McpServerConfig


def _stub_summarize_with_model_route(*_: object, **__: object) -> tuple[str, str]:
    return "openai/mock-primary", "mock summary"


def test_run_resolver_agent_does_not_fallback_to_alert_entity_without_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCA_MODEL_ALIAS_CODEX", "openai/mock-primary")
    monkeypatch.setenv("RCA_MODEL_ALIAS_CLAUDE", "anthropic/mock-fallback")
    monkeypatch.setattr(
        "platform_core.agent_runtime.summarize_with_model_route",
        _stub_summarize_with_model_route,
    )

    def _stub_execute(*_: object, **__: object) -> tuple[dict, AgentToolTrace]:
        started = datetime.now(timezone.utc)
        return (
            {"content": [{"type": "text", "text": "[]"}]},
            AgentToolTrace(
                tool_name="mcp.jaeger.get_services",
                source="mcp",
                read_only=True,
                started_at=started,
                ended_at=started,
                duration_ms=5,
                success=True,
                args_summary={},
                result_summary={"service_count": 0},
                error=None,
                citations=[],
            ),
        )

    monkeypatch.setattr("platform_core.agent_runtime._execute_mcp_tool", _stub_execute)

    result = run_resolver_agent(
        alert_payload={
            "source": "demo",
            "severity": "critical",
            "incident_key": "inc-no-alias",
            "entity_ids": ["service-checkout"],
            "timestamps": {"triggered_at": datetime.now(timezone.utc).isoformat()},
            "raw_payload_ref": "demo://alert",
            "raw_payload": {"service": "service-checkout", "env": "prod"},
        },
        model_route=ModelRoute(primary="codex", fallback="claude"),
        prompt_profile=AgentPromptProfile(
            tenant="default",
            environment="prod",
            stage_id="resolve_service_identity",
            system_prompt="test",
            objective_template="test",
            max_turns=2,
            max_tool_calls=2,
            tool_allowlist=["mcp.jaeger.*"],
            updated_at=datetime.now(timezone.utc),
            updated_by="test",
        ),
        mcp_servers=[
            McpServerConfig(
                server_id="jaeger",
                tenant="default",
                environment="prod",
                transport="http_sse",
                base_url="http://jaeger-mcp:8000/mcp",
                timeout_seconds=8,
                enabled=True,
                updated_at=datetime.now(timezone.utc),
                updated_by="test",
            )
        ],
        mcp_tools=[
            McpToolDescriptor(
                server_id="jaeger",
                tool_name="get_services",
                description="discover services",
                capabilities=["tracing"],
                read_only=True,
                light_probe=True,
                arg_keys=[],
                required_args=[],
            )
        ],
    )

    assert result.payload["canonical_service_id"] == "unknown"
    assert result.payload["confidence"] == 0.0
    assert result.payload["mapped_provider_ids"] == {}
    assert result.artifact_state["alias_decision_trace"]["unresolved_reason"] == "no_service_candidates"
