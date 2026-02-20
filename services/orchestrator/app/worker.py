from __future__ import annotations

import os

from temporalio.client import Client
from temporalio.worker import Worker

from services.orchestrator.app.activities import (
    build_plan_activity,
    collect_evidence_activity,
    emit_eval_event_activity,
    publish_activity,
    report_workflow_terminal_activity,
    resolve_service_activity,
    synthesize_report_activity,
)
from services.orchestrator.app.workflow import InvestigationWorkflow


async def run_worker() -> None:
    temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "rca-investigations")
    client = await Client.connect(temporal_address)
    worker = Worker(
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
            report_workflow_terminal_activity,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    import asyncio

    asyncio.run(run_worker())
