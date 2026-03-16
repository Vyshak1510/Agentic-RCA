from __future__ import annotations

from datetime import datetime, timezone

from platform_core.models import AgentPromptProfile, WorkflowStageId
from platform_core.store import InMemoryStore


def test_system_prompt_profiles_are_reconciled_with_prometheus_allowlist(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RCA_STORE_STATE_PATH", str(tmp_path / "store-state.json"))
    store = InMemoryStore()

    stale = AgentPromptProfile(
        tenant="default",
        environment="prod",
        stage_id=WorkflowStageId.BUILD_INVESTIGATION_PLAN,
        system_prompt="You are an RCA investigation agent. Use read-only tools and cite evidence.",
        objective_template="Resolve alert {{incident_key}} with bounded, evidence-linked reasoning.",
        max_turns=4,
        max_tool_calls=6,
        tool_allowlist=["mcp.grafana.*", "mcp.jaeger.*"],
        updated_at=datetime.now(timezone.utc),
        updated_by="system",
    )
    store.agent_prompt_profiles[(stale.tenant, stale.environment, stale.stage_id)] = stale

    reconciled = store.get_agent_prompt_profile("default", "prod", WorkflowStageId.BUILD_INVESTIGATION_PLAN)

    assert reconciled is not None
    assert "mcp.prometheus.*" in reconciled.tool_allowlist

