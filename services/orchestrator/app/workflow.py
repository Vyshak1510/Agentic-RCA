from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from platform_core.models import InvestigationStatus

with workflow.unsafe.imports_passed_through():
    from services.orchestrator.app.activities import (
        build_plan_activity,
        collect_evidence_activity,
        emit_eval_event_activity,
        publish_activity,
        report_workflow_terminal_activity,
        resolve_service_activity,
        synthesize_report_activity,
    )


@dataclass
class InvestigationWorkflowInput:
    investigation_id: str
    run_id: str
    workflow_id: str
    alert: dict[str, Any]
    publish_outputs: bool = True
    event_callback_url: str | None = None
    event_callback_token: str | None = None
    event_timeout_seconds: int = 3
    tenant: str = "default"
    environment: str = "prod"
    llm_route: dict[str, Any] | None = None
    agent_prompt_profiles: dict[str, dict[str, Any]] | None = None
    agent_rollout_mode: str = "compare"
    mcp_servers: list[dict[str, Any]] | None = None
    mcp_tools: list[dict[str, Any]] | None = None
    execution_policy: str = "mcp_only"

    def run_context(self) -> dict[str, Any]:
        return {
            "investigation_id": self.investigation_id,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "event_callback_url": self.event_callback_url,
            "event_callback_token": self.event_callback_token,
            "event_timeout_seconds": self.event_timeout_seconds,
            "tenant": self.tenant,
            "environment": self.environment,
            "llm_route": self.llm_route or {},
            "agent_prompt_profiles": self.agent_prompt_profiles or {},
            "agent_rollout_mode": self.agent_rollout_mode,
            "mcp_servers": self.mcp_servers or [],
            "mcp_tools": self.mcp_tools or [],
            "execution_policy": self.execution_policy,
        }


ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=5),
    maximum_attempts=3,
)


@workflow.defn
class InvestigationWorkflow:
    async def _execute_activity(
        self,
        fn: Any,
        *args: Any,
        timeout_seconds: int = 30,
    ) -> Any:
        return await workflow.execute_activity(
            fn,
            args=args,
            start_to_close_timeout=timedelta(seconds=timeout_seconds),
            retry_policy=ACTIVITY_RETRY_POLICY,
        )

    @workflow.run
    async def run(self, wf_input: InvestigationWorkflowInput) -> dict[str, Any]:
        started_at = workflow.now()
        timeline: list[str] = ["Investigation started"]
        run_context = wf_input.run_context()

        try:
            service_identity = await self._execute_activity(
                resolve_service_activity,
                run_context,
                wf_input.alert,
                timeout_seconds=20,
            )
            timeline.append("Service identity resolved")

            plan = await self._execute_activity(
                build_plan_activity,
                run_context,
                wf_input.investigation_id,
                wf_input.alert,
                timeout_seconds=20,
            )
            timeline.append("Bounded investigation plan generated")

            evidence_result = await self._execute_activity(
                collect_evidence_activity,
                run_context,
                wf_input.investigation_id,
                wf_input.alert,
                plan,
                timeout_seconds=120,
            )
            timeline.extend(evidence_result["timeline"])

            synthesis = await self._execute_activity(
                synthesize_report_activity,
                run_context,
                wf_input.alert,
                service_identity,
                evidence_result["evidence"],
                timeout_seconds=30,
            )
            timeline.append(f"RCA synthesized using model: {synthesis['llm_model_used']}")

            publish_result = await self._execute_activity(
                publish_activity,
                run_context,
                wf_input.alert,
                synthesis["report"],
                wf_input.publish_outputs,
                timeout_seconds=15,
            )
            if publish_result["published"]:
                timeline.append("Slack/Jira publish completed")
            else:
                timeline.append("External publish skipped")

            latency_seconds = (workflow.now() - started_at).total_seconds()
            eval_event = await self._execute_activity(
                emit_eval_event_activity,
                run_context,
                wf_input.investigation_id,
                synthesis["report"],
                evidence_result["evidence"],
                latency_seconds,
                timeout_seconds=15,
            )
            timeline.append("Eval event emitted")

            terminal_event = await self._execute_activity(
                report_workflow_terminal_activity,
                run_context,
                InvestigationStatus.COMPLETED,
                "Workflow completed",
                latency_seconds,
                timeout_seconds=10,
            )

            return {
                "status": "completed",
                "investigation_id": wf_input.investigation_id,
                "run_id": wf_input.run_id,
                "workflow_id": wf_input.workflow_id,
                "started_at": started_at.isoformat(),
                "completed_at": workflow.now().isoformat(),
                "latency_seconds": round(latency_seconds, 2),
                "service_identity": service_identity,
                "plan": plan,
                "evidence_count": len(evidence_result["evidence"]),
                "executed_steps": evidence_result["executed_steps"],
                "stopped_early": evidence_result["stopped_early"],
                "report": synthesis["report"],
                "publish_result": publish_result,
                "eval_event": eval_event,
                "terminal_event": terminal_event,
                "timeline": timeline,
            }
        except Exception as exc:
            latency_seconds = (workflow.now() - started_at).total_seconds()
            await self._execute_activity(
                report_workflow_terminal_activity,
                run_context,
                InvestigationStatus.FAILED,
                f"Workflow failed: {exc}",
                latency_seconds,
                timeout_seconds=10,
            )
            raise
