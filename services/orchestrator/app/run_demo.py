from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from temporalio.client import Client
from temporalio.worker import Worker

from services.orchestrator.app.activities import (
    build_plan_activity,
    collect_evidence_activity,
    emit_eval_event_activity,
    publish_activity,
    resolve_service_activity,
    synthesize_report_activity,
)
from services.orchestrator.app.workflow import InvestigationWorkflow, InvestigationWorkflowInput


async def run_demo() -> None:
    temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "rca-investigations")
    publish_outputs = os.getenv("DEMO_PUBLISH_OUTPUTS", "false").lower() in {"1", "true", "yes"}

    investigation_id = f"inv-{uuid4()}"
    workflow_id = f"rca-demo-{uuid4()}"
    wf_input = InvestigationWorkflowInput(
        investigation_id=investigation_id,
        alert={
            "source": "newrelic",
            "severity": "critical",
            "incident_key": f"demo-{uuid4()}",
            "entity_ids": ["service-checkout"],
            "timestamps": {"triggered_at": datetime.now(timezone.utc).isoformat()},
            "raw_payload_ref": "newrelic://demo",
            "raw_payload": {"condition": "error_rate_spike"},
        },
        publish_outputs=publish_outputs,
    )

    client = await Client.connect(temporal_address)
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[InvestigationWorkflow],
        activities=[
            resolve_service_activity,
            build_plan_activity,
            collect_evidence_activity,
            synthesize_report_activity,
            publish_activity,
            emit_eval_event_activity,
        ],
    ):
        result = await client.execute_workflow(
            InvestigationWorkflow.run,
            wf_input,
            id=workflow_id,
            task_queue=task_queue,
            execution_timeout=timedelta(minutes=5),
        )

    print(
        json.dumps(
            {
                "workflow_id": workflow_id,
                "investigation_id": investigation_id,
                "task_queue": task_queue,
                "result": result,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(run_demo())
