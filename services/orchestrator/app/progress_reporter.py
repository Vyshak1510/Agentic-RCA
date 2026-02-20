from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from platform_core.models import InvestigationStatus, StepExecutionStatus, WorkflowStageId


def _normalize_run_status(run_status: Any) -> str | None:
    if run_status is None:
        return None

    if isinstance(run_status, InvestigationStatus):
        return run_status.value

    if isinstance(run_status, list):
        if not run_status:
            return None
        first = run_status[0]
        if isinstance(first, InvestigationStatus):
            return first.value
        return str(first)

    if hasattr(run_status, "value"):
        return str(run_status.value)

    return str(run_status)


async def report_stage_event(
    *,
    run_context: dict[str, Any],
    stage_id: WorkflowStageId | None,
    stage_status: StepExecutionStatus,
    message: str,
    attempt: int = 1,
    metadata: dict[str, Any] | None = None,
    citations: list[str] | None = None,
    error: str | None = None,
    run_status: InvestigationStatus | None = None,
) -> bool:
    event_url = str(run_context.get("event_callback_url") or "").strip()
    run_id = str(run_context.get("run_id") or "").strip()
    investigation_id = str(run_context.get("investigation_id") or "").strip()
    workflow_id = str(run_context.get("workflow_id") or "").strip() or None

    if not event_url or not run_id or not investigation_id:
        return False

    payload = {
        "run_id": run_id,
        "investigation_id": investigation_id,
        "workflow_id": workflow_id,
        "stage_id": stage_id.value if stage_id else None,
        "stage_status": stage_status.value,
        "run_status": _normalize_run_status(run_status),
        "attempt": attempt,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "error": error,
        "citations": citations or [],
        "metadata": metadata or {},
        "logs": [],
        "stage_reasoning_summary": (metadata or {}).get("stage_reasoning_summary"),
        "tool_traces": (metadata or {}).get("tool_traces", []),
    }

    headers = {"content-type": "application/json"}
    token = str(run_context.get("event_callback_token") or "").strip()
    if token:
        headers["x-internal-token"] = token

    timeout_seconds = float(run_context.get("event_timeout_seconds") or 3)

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(event_url, json=payload, headers=headers)
            response.raise_for_status()
    except Exception:
        return False

    return True
