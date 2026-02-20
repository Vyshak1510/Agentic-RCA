from __future__ import annotations

from typing import Any, Callable

from temporalio import activity

from platform_core.models import InvestigationStatus, StepExecutionStatus, WorkflowStageId
from services.orchestrator.app.pipeline import (
    build_plan_stage,
    collect_evidence_stage,
    emit_eval_event_stage,
    publish_stage,
    resolve_service_stage,
    synthesize_report_stage,
)
from services.orchestrator.app.progress_reporter import report_stage_event


def _attempt_number() -> int:
    try:
        return activity.info().attempt
    except Exception:
        return 1


def _completion_message(stage_id: WorkflowStageId, result: Any) -> str:
    if stage_id == WorkflowStageId.RESOLVE_SERVICE_IDENTITY:
        return "Service identity resolved"
    if stage_id == WorkflowStageId.BUILD_INVESTIGATION_PLAN:
        step_count = len(result.get("ordered_steps", [])) if isinstance(result, dict) else 0
        return f"Bounded investigation plan generated ({step_count} steps)"
    if stage_id == WorkflowStageId.COLLECT_EVIDENCE:
        evidence_count = len(result.get("evidence", [])) if isinstance(result, dict) else 0
        return f"Collected {evidence_count} evidence item(s)"
    if stage_id == WorkflowStageId.SYNTHESIZE_RCA_REPORT:
        model_used = result.get("llm_model_used") if isinstance(result, dict) else "unknown"
        return f"RCA synthesized using model: {model_used}"
    if stage_id == WorkflowStageId.PUBLISH_REPORT:
        published = bool(result.get("published")) if isinstance(result, dict) else False
        return "Slack/Jira publish completed" if published else "External publish skipped"
    if stage_id == WorkflowStageId.EMIT_EVAL_EVENT:
        return "Eval event emitted"
    return f"{stage_id.value} completed"


def _metadata(stage_id: WorkflowStageId, result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}

    if stage_id == WorkflowStageId.RESOLVE_SERVICE_IDENTITY:
        metadata = {
            "canonical_service_id": result.get("canonical_service_id"),
            "confidence": result.get("confidence"),
        }
        if result.get("agent_rollout_mode"):
            metadata["agent_rollout_mode"] = result.get("agent_rollout_mode")
        if result.get("agent_compare"):
            metadata["agent_compare"] = result.get("agent_compare")
        if result.get("llm_model_used"):
            metadata["llm_model_used"] = result.get("llm_model_used")
        if result.get("stage_reasoning_summary"):
            metadata["stage_reasoning_summary"] = result.get("stage_reasoning_summary")
        if result.get("tool_traces"):
            metadata["tool_traces"] = result.get("tool_traces")
        return metadata
    if stage_id == WorkflowStageId.BUILD_INVESTIGATION_PLAN:
        metadata = {
            "step_count": len(result.get("ordered_steps", [])),
            "max_api_calls": result.get("max_api_calls"),
            "max_stage_wall_clock_seconds": result.get("max_stage_wall_clock_seconds"),
        }
        if result.get("agent_rollout_mode"):
            metadata["agent_rollout_mode"] = result.get("agent_rollout_mode")
        if result.get("agent_compare"):
            metadata["agent_compare"] = result.get("agent_compare")
        if result.get("llm_model_used"):
            metadata["llm_model_used"] = result.get("llm_model_used")
        if result.get("stage_reasoning_summary"):
            metadata["stage_reasoning_summary"] = result.get("stage_reasoning_summary")
        if result.get("tool_traces"):
            metadata["tool_traces"] = result.get("tool_traces")
        return metadata
    if stage_id == WorkflowStageId.COLLECT_EVIDENCE:
        return {
            "executed_steps": result.get("executed_steps"),
            "stopped_early": result.get("stopped_early"),
            "evidence_count": len(result.get("evidence", [])),
        }
    if stage_id == WorkflowStageId.SYNTHESIZE_RCA_REPORT:
        report = result.get("report", {})
        return {
            "llm_model_used": result.get("llm_model_used"),
            "hypothesis_count": len(report.get("top_hypotheses", [])) if isinstance(report, dict) else 0,
            "confidence": report.get("confidence") if isinstance(report, dict) else None,
        }
    if stage_id == WorkflowStageId.PUBLISH_REPORT:
        return {
            "published": result.get("published"),
            "slack_message_id": result.get("slack_message_id"),
            "jira_issue_key": result.get("jira_issue_key"),
        }
    if stage_id == WorkflowStageId.EMIT_EVAL_EVENT:
        return {
            "top_hypothesis_count": result.get("top_hypothesis_count"),
            "citation_count": result.get("citation_count"),
            "evidence_count": result.get("evidence_count"),
            "latency_seconds": result.get("latency_seconds"),
            "rollout_mode": result.get("rollout_mode"),
        }

    return {}


def _citations(stage_id: WorkflowStageId, result: Any) -> list[str]:
    if not isinstance(result, dict):
        return []

    if stage_id == WorkflowStageId.COLLECT_EVIDENCE:
        evidence = result.get("evidence", [])
        if not isinstance(evidence, list):
            return []
        return [
            str(item.get("citation_id"))
            for item in evidence
            if isinstance(item, dict) and item.get("citation_id")
        ]

    if stage_id == WorkflowStageId.SYNTHESIZE_RCA_REPORT:
        report = result.get("report", {})
        if not isinstance(report, dict):
            return []
        hypotheses = report.get("top_hypotheses", [])
        if not isinstance(hypotheses, list):
            return []

        citations: list[str] = []
        for hypothesis in hypotheses:
            if not isinstance(hypothesis, dict):
                continue
            for citation in hypothesis.get("supporting_citations", []):
                if citation not in citations:
                    citations.append(citation)
        return citations

        return []


def _normalize_investigation_status(value: Any) -> InvestigationStatus:
    if isinstance(value, InvestigationStatus):
        return value
    if isinstance(value, list):
        if not value:
            return InvestigationStatus.FAILED
        if all(isinstance(item, str) for item in value):
            joined = "".join(value).strip().lower()
            if joined in {status.value for status in InvestigationStatus}:
                return InvestigationStatus(joined)
        value = value[0]
    return InvestigationStatus(str(value))


async def _execute_with_progress(
    *,
    stage_id: WorkflowStageId,
    run_context: dict[str, Any],
    fn: Callable[..., Any],
    args: tuple[Any, ...],
) -> Any:
    attempt = _attempt_number()
    await report_stage_event(
        run_context=run_context,
        stage_id=stage_id,
        stage_status=StepExecutionStatus.RUNNING,
        message=f"{stage_id.value} started",
        attempt=attempt,
    )

    try:
        result = fn(*args)
    except Exception as exc:
        await report_stage_event(
            run_context=run_context,
            stage_id=stage_id,
            stage_status=StepExecutionStatus.FAILED,
            message=f"{stage_id.value} failed",
            attempt=attempt,
            error=str(exc),
            metadata={"exception_type": exc.__class__.__name__},
        )
        raise

    await report_stage_event(
        run_context=run_context,
        stage_id=stage_id,
        stage_status=StepExecutionStatus.COMPLETED,
        message=_completion_message(stage_id, result),
        attempt=attempt,
        metadata=_metadata(stage_id, result),
        citations=_citations(stage_id, result),
    )
    return result


@activity.defn(name="resolve-service-identity")
async def resolve_service_activity(run_context: dict[str, Any], alert_payload: dict[str, Any]) -> dict[str, Any]:
    return await _execute_with_progress(
        stage_id=WorkflowStageId.RESOLVE_SERVICE_IDENTITY,
        run_context=run_context,
        fn=resolve_service_stage,
        args=(alert_payload, run_context),
    )


@activity.defn(name="build-investigation-plan")
async def build_plan_activity(
    run_context: dict[str, Any],
    investigation_id: str,
    alert_payload: dict[str, Any],
) -> dict[str, Any]:
    return await _execute_with_progress(
        stage_id=WorkflowStageId.BUILD_INVESTIGATION_PLAN,
        run_context=run_context,
        fn=build_plan_stage,
        args=(investigation_id, alert_payload, run_context),
    )


@activity.defn(name="collect-evidence")
async def collect_evidence_activity(
    run_context: dict[str, Any],
    investigation_id: str,
    alert_payload: dict[str, Any],
    plan_payload: dict[str, Any],
) -> dict[str, Any]:
    return await _execute_with_progress(
        stage_id=WorkflowStageId.COLLECT_EVIDENCE,
        run_context=run_context,
        fn=collect_evidence_stage,
        args=(investigation_id, alert_payload, plan_payload),
    )


@activity.defn(name="synthesize-rca-report")
async def synthesize_report_activity(
    run_context: dict[str, Any],
    alert_payload: dict[str, Any],
    service_identity_payload: dict[str, Any],
    evidence_payload: list[dict[str, Any]],
) -> dict[str, Any]:
    return await _execute_with_progress(
        stage_id=WorkflowStageId.SYNTHESIZE_RCA_REPORT,
        run_context=run_context,
        fn=synthesize_report_stage,
        args=(alert_payload, service_identity_payload, evidence_payload),
    )


@activity.defn(name="publish-report")
async def publish_activity(
    run_context: dict[str, Any],
    alert_payload: dict[str, Any],
    report_payload: dict[str, Any],
    enabled: bool = True,
) -> dict[str, Any]:
    return await _execute_with_progress(
        stage_id=WorkflowStageId.PUBLISH_REPORT,
        run_context=run_context,
        fn=publish_stage,
        args=(alert_payload, report_payload, enabled),
    )


@activity.defn(name="emit-eval-event")
async def emit_eval_event_activity(
    run_context: dict[str, Any],
    investigation_id: str,
    report_payload: dict[str, Any],
    evidence_payload: list[dict[str, Any]],
    latency_seconds: float,
) -> dict[str, Any]:
    return await _execute_with_progress(
        stage_id=WorkflowStageId.EMIT_EVAL_EVENT,
        run_context=run_context,
        fn=emit_eval_event_stage,
        args=(investigation_id, report_payload, evidence_payload, latency_seconds),
    )


@activity.defn(name="report-workflow-terminal")
async def report_workflow_terminal_activity(
    run_context: dict[str, Any],
    status: InvestigationStatus,
    message: str,
    latency_seconds: float,
) -> dict[str, Any]:
    normalized_status = _normalize_investigation_status(status)
    attempt = _attempt_number()
    stage_status = (
        StepExecutionStatus.COMPLETED
        if normalized_status == InvestigationStatus.COMPLETED
        else StepExecutionStatus.FAILED
    )
    await report_stage_event(
        run_context=run_context,
        stage_id=None,
        stage_status=stage_status,
        run_status=normalized_status,
        message=message,
        attempt=attempt,
        metadata={"latency_seconds": latency_seconds},
        error=message if normalized_status == InvestigationStatus.FAILED else None,
    )
    return {
        "status": normalized_status.value,
        "message": message,
        "latency_seconds": latency_seconds,
    }
