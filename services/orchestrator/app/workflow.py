from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from services.orchestrator.app.activities import (
        build_plan_activity,
        collect_evidence_activity,
        emit_eval_event_activity,
        publish_activity,
        resolve_service_activity,
        synthesize_report_activity,
    )


@dataclass
class InvestigationWorkflowInput:
    investigation_id: str
    alert: dict[str, Any]
    publish_outputs: bool = True


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

        service_identity = await self._execute_activity(
            resolve_service_activity,
            wf_input.alert,
            timeout_seconds=20,
        )
        timeline.append("Service identity resolved")

        plan = await self._execute_activity(
            build_plan_activity,
            wf_input.investigation_id,
            wf_input.alert,
            timeout_seconds=20,
        )
        timeline.append("Bounded investigation plan generated")

        evidence_result = await self._execute_activity(
            collect_evidence_activity,
            wf_input.investigation_id,
            wf_input.alert,
            plan,
            timeout_seconds=120,
        )
        timeline.extend(evidence_result["timeline"])

        synthesis = await self._execute_activity(
            synthesize_report_activity,
            wf_input.alert,
            service_identity,
            evidence_result["evidence"],
            timeout_seconds=30,
        )
        timeline.append(f"RCA synthesized using model: {synthesis['llm_model_used']}")

        publish_result = await self._execute_activity(
            publish_activity,
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
            wf_input.investigation_id,
            synthesis["report"],
            evidence_result["evidence"],
            latency_seconds,
            timeout_seconds=15,
        )
        timeline.append("Eval event emitted")

        return {
            "status": "completed",
            "investigation_id": wf_input.investigation_id,
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
            "timeline": timeline,
        }
