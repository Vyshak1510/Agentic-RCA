from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from platform_core.models import InvestigationStatus, WorkflowStageId

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
    investigation_teams: list[dict[str, Any]] | None = None
    stage_missions: dict[str, dict[str, Any]] | None = None
    team_missions: dict[str, dict[str, Any]] | None = None
    active_context_pack: dict[str, Any] | None = None
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
            "investigation_teams": self.investigation_teams or [],
            "stage_missions": self.stage_missions or {},
            "team_missions": self.team_missions or {},
            "active_context_pack": self.active_context_pack or {},
            "execution_policy": self.execution_policy,
        }


ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=5),
    maximum_attempts=3,
)

MAX_TOTAL_RERUN_DIRECTIVES = 2
MAX_STAGE_RERUNS = 1


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

    def _prepare_stage_invocation(self, run_context: dict[str, Any], stage_id: WorkflowStageId) -> None:
        overrides = run_context.setdefault("stage_attempt_overrides", {})
        overrides[stage_id.value] = int(overrides.get(stage_id.value, 0)) + 1

    def _record_stage_result(
        self,
        run_context: dict[str, Any],
        stage_id: WorkflowStageId,
        result: dict[str, Any],
    ) -> None:
        stage_results = run_context.setdefault("stage_results", {})
        stage_results[stage_id.value] = result
        if isinstance(result.get("effective_prompt_snapshot"), dict):
            run_context.setdefault("effective_prompt_profiles", {})[stage_id.value] = result["effective_prompt_snapshot"]
        if isinstance(result.get("effective_mission_snapshot"), dict):
            run_context.setdefault("effective_stage_missions", {})[stage_id.value] = result["effective_mission_snapshot"]
        if isinstance(result.get("effective_team_mission_snapshots"), dict):
            run_context.setdefault("effective_team_missions", {}).update(result["effective_team_mission_snapshots"])
        if isinstance(result.get("stage_eval_records"), list):
            run_context.setdefault("stage_eval_records", [])
            run_context["stage_eval_records"] = [
                *[item for item in run_context["stage_eval_records"] if item.get("stage_id") != stage_id.value],
                *[item for item in result["stage_eval_records"] if isinstance(item, dict)],
            ]
        if result.get("alias_decision_trace"):
            run_context["alias_decision_trace"] = result.get("alias_decision_trace")

    def _consume_rerun_directive(
        self,
        run_context: dict[str, Any],
        *,
        requested_by_stage: WorkflowStageId,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        directives = result.get("rerun_directives")
        if str(run_context.get("agent_rollout_mode") or "compare") != "active":
            return None
        if not isinstance(directives, list) or not directives:
            return None
        rerun_ledger = run_context.setdefault("rerun_ledger", [])
        if len(rerun_ledger) >= MAX_TOTAL_RERUN_DIRECTIVES:
            return None
        rerun_counts = run_context.setdefault("rerun_stage_counts", {})
        for directive in directives:
            if not isinstance(directive, dict):
                continue
            target_stage = str(directive.get("target_stage") or "").strip()
            if target_stage not in {stage.value for stage in WorkflowStageId}:
                continue
            if int(rerun_counts.get(target_stage, 0)) >= MAX_STAGE_RERUNS:
                continue
            rerun_counts[target_stage] = int(rerun_counts.get(target_stage, 0)) + 1
            ledger_entry = {
                "sequence": len(rerun_ledger) + 1,
                "requested_by_stage": requested_by_stage.value,
                "target_stage": target_stage,
                "reason": directive.get("reason"),
                "additional_objective": directive.get("additional_objective"),
                "expected_evidence": directive.get("expected_evidence"),
                "tool_focus": directive.get("tool_focus", []),
                "accepted": True,
                "outcome": "accepted",
                "requested_at": workflow.now().isoformat(),
                "completed_at": None,
            }
            rerun_ledger.append(ledger_entry)
            run_context["active_rerun_directive"] = directive
            return ledger_entry
        return None

    def _complete_rerun_directive(self, run_context: dict[str, Any], outcome: str) -> None:
        rerun_ledger = run_context.get("rerun_ledger")
        if not isinstance(rerun_ledger, list) or not rerun_ledger:
            return
        rerun_ledger[-1]["outcome"] = outcome
        rerun_ledger[-1]["completed_at"] = workflow.now().isoformat()
        run_context["active_rerun_directive"] = {}

    @workflow.run
    async def run(self, wf_input: InvestigationWorkflowInput) -> dict[str, Any]:
        started_at = workflow.now()
        timeline: list[str] = ["Investigation started"]
        run_context = wf_input.run_context()

        try:
            self._prepare_stage_invocation(run_context, WorkflowStageId.RESOLVE_SERVICE_IDENTITY)
            service_identity = await self._execute_activity(resolve_service_activity, run_context, wf_input.alert, timeout_seconds=20)
            self._record_stage_result(run_context, WorkflowStageId.RESOLVE_SERVICE_IDENTITY, service_identity)
            run_context["service_identity"] = service_identity
            if isinstance(service_identity, dict) and isinstance(service_identity.get("artifact_state"), dict):
                run_context["resolver_artifact_state"] = service_identity["artifact_state"]
            timeline.append("Service identity resolved")

            self._prepare_stage_invocation(run_context, WorkflowStageId.BUILD_INVESTIGATION_PLAN)
            plan = await self._execute_activity(build_plan_activity, run_context, wf_input.investigation_id, wf_input.alert, timeout_seconds=20)
            self._record_stage_result(run_context, WorkflowStageId.BUILD_INVESTIGATION_PLAN, plan)
            if isinstance(plan, dict) and isinstance(plan.get("artifact_state"), dict):
                run_context["planner_artifact_state"] = plan["artifact_state"]
            timeline.append("Bounded investigation plan generated")
            while True:
                rerun = self._consume_rerun_directive(
                    run_context,
                    requested_by_stage=WorkflowStageId.BUILD_INVESTIGATION_PLAN,
                    result=plan,
                )
                if not rerun:
                    break
                timeline.append(
                    f"Planner requested rerun of {rerun['target_stage']}: {rerun.get('reason') or 'additional investigation needed'}"
                )
                if rerun["target_stage"] == WorkflowStageId.RESOLVE_SERVICE_IDENTITY.value:
                    self._prepare_stage_invocation(run_context, WorkflowStageId.RESOLVE_SERVICE_IDENTITY)
                    service_identity = await self._execute_activity(resolve_service_activity, run_context, wf_input.alert, timeout_seconds=20)
                    self._record_stage_result(run_context, WorkflowStageId.RESOLVE_SERVICE_IDENTITY, service_identity)
                    run_context["service_identity"] = service_identity
                    if isinstance(service_identity, dict) and isinstance(service_identity.get("artifact_state"), dict):
                        run_context["resolver_artifact_state"] = service_identity["artifact_state"]
                    timeline.append("Service identity re-resolved")
                self._prepare_stage_invocation(run_context, WorkflowStageId.BUILD_INVESTIGATION_PLAN)
                plan = await self._execute_activity(build_plan_activity, run_context, wf_input.investigation_id, wf_input.alert, timeout_seconds=20)
                self._record_stage_result(run_context, WorkflowStageId.BUILD_INVESTIGATION_PLAN, plan)
                if isinstance(plan, dict) and isinstance(plan.get("artifact_state"), dict):
                    run_context["planner_artifact_state"] = plan["artifact_state"]
                self._complete_rerun_directive(run_context, "completed")
                timeline.append("Investigation plan regenerated after rerun")
            if not bool(plan.get("plan_valid", True)):
                raise RuntimeError(f"Planner validation failed: {', '.join(plan.get('plan_validation_errors', []))}")

            self._prepare_stage_invocation(run_context, WorkflowStageId.COLLECT_EVIDENCE)
            evidence_result = await self._execute_activity(
                collect_evidence_activity,
                run_context,
                wf_input.investigation_id,
                wf_input.alert,
                plan,
                timeout_seconds=120,
            )
            self._record_stage_result(run_context, WorkflowStageId.COLLECT_EVIDENCE, evidence_result)
            timeline.extend(evidence_result["timeline"])

            self._prepare_stage_invocation(run_context, WorkflowStageId.SYNTHESIZE_RCA_REPORT)
            synthesis = await self._execute_activity(
                synthesize_report_activity,
                run_context,
                wf_input.alert,
                service_identity,
                evidence_result["evidence"],
                evidence_result,
                timeout_seconds=90,
            )
            self._record_stage_result(run_context, WorkflowStageId.SYNTHESIZE_RCA_REPORT, synthesis)
            timeline.append(f"RCA synthesized using model: {synthesis['llm_model_used']}")
            while True:
                rerun = self._consume_rerun_directive(
                    run_context,
                    requested_by_stage=WorkflowStageId.SYNTHESIZE_RCA_REPORT,
                    result=synthesis,
                )
                if not rerun:
                    break
                timeline.append(
                    f"Commander requested rerun of {rerun['target_stage']}: {rerun.get('reason') or 'additional investigation needed'}"
                )
                if rerun["target_stage"] == WorkflowStageId.RESOLVE_SERVICE_IDENTITY.value:
                    self._prepare_stage_invocation(run_context, WorkflowStageId.RESOLVE_SERVICE_IDENTITY)
                    service_identity = await self._execute_activity(resolve_service_activity, run_context, wf_input.alert, timeout_seconds=20)
                    self._record_stage_result(run_context, WorkflowStageId.RESOLVE_SERVICE_IDENTITY, service_identity)
                    run_context["service_identity"] = service_identity
                    if isinstance(service_identity, dict) and isinstance(service_identity.get("artifact_state"), dict):
                        run_context["resolver_artifact_state"] = service_identity["artifact_state"]
                    timeline.append("Service identity re-resolved")
                    self._prepare_stage_invocation(run_context, WorkflowStageId.BUILD_INVESTIGATION_PLAN)
                    plan = await self._execute_activity(build_plan_activity, run_context, wf_input.investigation_id, wf_input.alert, timeout_seconds=20)
                    self._record_stage_result(run_context, WorkflowStageId.BUILD_INVESTIGATION_PLAN, plan)
                    if not bool(plan.get("plan_valid", True)):
                        raise RuntimeError(f"Planner validation failed after rerun: {', '.join(plan.get('plan_validation_errors', []))}")
                    if isinstance(plan, dict) and isinstance(plan.get("artifact_state"), dict):
                        run_context["planner_artifact_state"] = plan["artifact_state"]
                    timeline.append("Investigation plan regenerated after service rerun")
                elif rerun["target_stage"] == WorkflowStageId.BUILD_INVESTIGATION_PLAN.value:
                    self._prepare_stage_invocation(run_context, WorkflowStageId.BUILD_INVESTIGATION_PLAN)
                    plan = await self._execute_activity(build_plan_activity, run_context, wf_input.investigation_id, wf_input.alert, timeout_seconds=20)
                    self._record_stage_result(run_context, WorkflowStageId.BUILD_INVESTIGATION_PLAN, plan)
                    if not bool(plan.get("plan_valid", True)):
                        raise RuntimeError(f"Planner validation failed after rerun: {', '.join(plan.get('plan_validation_errors', []))}")
                    if isinstance(plan, dict) and isinstance(plan.get("artifact_state"), dict):
                        run_context["planner_artifact_state"] = plan["artifact_state"]
                    timeline.append("Investigation plan regenerated")

                self._prepare_stage_invocation(run_context, WorkflowStageId.COLLECT_EVIDENCE)
                evidence_result = await self._execute_activity(
                    collect_evidence_activity,
                    run_context,
                    wf_input.investigation_id,
                    wf_input.alert,
                    plan,
                    timeout_seconds=120,
                )
                self._record_stage_result(run_context, WorkflowStageId.COLLECT_EVIDENCE, evidence_result)
                timeline.extend(evidence_result["timeline"])

                self._prepare_stage_invocation(run_context, WorkflowStageId.SYNTHESIZE_RCA_REPORT)
                synthesis = await self._execute_activity(
                    synthesize_report_activity,
                    run_context,
                    wf_input.alert,
                    service_identity,
                    evidence_result["evidence"],
                    evidence_result,
                    timeout_seconds=90,
                )
                self._record_stage_result(run_context, WorkflowStageId.SYNTHESIZE_RCA_REPORT, synthesis)
                self._complete_rerun_directive(run_context, "completed")
                timeline.append(f"RCA re-synthesized using model: {synthesis['llm_model_used']}")

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
