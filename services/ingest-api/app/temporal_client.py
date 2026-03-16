from __future__ import annotations

from os import getenv

def _event_callback_url() -> str | None:
    base_url = getenv("ORCHESTRATOR_EVENT_BASE_URL", "http://localhost:8000").strip()
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}/v1/internal/runs/events"


async def start_investigation_workflow(
    *,
    investigation_id: str,
    run_id: str,
    workflow_id: str,
    alert_payload: dict,
    publish_outputs: bool,
    tenant: str = "default",
    environment: str = "prod",
    llm_route: dict | None = None,
    agent_prompt_profiles: dict | None = None,
    agent_rollout_mode: str = "compare",
    mcp_servers: list[dict] | None = None,
    mcp_tools: list[dict] | None = None,
    investigation_teams: list[dict] | None = None,
    stage_missions: dict | None = None,
    team_missions: dict | None = None,
    active_context_pack: dict | None = None,
    execution_policy: str = "mcp_only",
) -> tuple[str | None, str | None]:
    if getenv("TEMPORAL_AUTOSTART_ENABLED", "true").lower() in {"0", "false", "no"}:
        return None, "temporal autostart disabled by TEMPORAL_AUTOSTART_ENABLED"

    temporal_address = getenv("TEMPORAL_ADDRESS", "localhost:7233")
    task_queue = getenv("TEMPORAL_TASK_QUEUE", "rca-investigations")
    callback_url = _event_callback_url()
    callback_token = getenv("ORCHESTRATOR_EVENT_TOKEN")

    try:
        from temporalio.client import Client

        from services.orchestrator.app.workflow import InvestigationWorkflow, InvestigationWorkflowInput

        client = await Client.connect(temporal_address)
        wf_input = InvestigationWorkflowInput(
            investigation_id=investigation_id,
            run_id=run_id,
            workflow_id=workflow_id,
            alert=alert_payload,
            publish_outputs=publish_outputs,
            event_callback_url=callback_url,
            event_callback_token=callback_token,
            tenant=tenant,
            environment=environment,
            llm_route=llm_route or {},
            agent_prompt_profiles=agent_prompt_profiles or {},
            agent_rollout_mode=agent_rollout_mode,
            mcp_servers=mcp_servers or [],
            mcp_tools=mcp_tools or [],
            investigation_teams=investigation_teams or [],
            stage_missions=stage_missions or {},
            team_missions=team_missions or {},
            active_context_pack=active_context_pack or {},
            execution_policy=execution_policy,
        )

        await client.start_workflow(
            InvestigationWorkflow.run,
            wf_input,
            id=workflow_id,
            task_queue=task_queue,
        )
        return workflow_id, None
    except Exception as exc:  # pragma: no cover - behavior validated by API contract tests
        return None, str(exc)
