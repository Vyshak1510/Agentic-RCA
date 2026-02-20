from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from platform_core.models import AdjudicationRecord, EvalRunRequest, EvalRunResult, GoldenIncident
from platform_core.store import store

app = FastAPI(title="rca-eval-service", version="0.1.0")

THRESHOLDS = {
    "top3": 0.65,
    "top1": 0.35,
    "unsupported": 0.05,
}


@app.get("/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "eval-service"}


@app.post("/v1/evals/runs")
def create_eval_run(req: EvalRunRequest) -> EvalRunResult:
    run_id = str(uuid4())
    started_at = datetime.now(timezone.utc)
    result = EvalRunResult(id=run_id, started_at=started_at)

    path = Path(req.dataset_path)
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"dataset not found: {req.dataset_path}")

    incidents = [GoldenIncident.model_validate(item) for item in json.loads(path.read_text())]
    total = max(len(incidents), 1)

    # Placeholder scoring for scaffold: assumes baseline model predicts expected cause for 40% top1 and 70% top3.
    result.top1_hit_rate = 0.40
    result.top3_hit_rate = 0.70
    result.unsupported_claim_rate = 0.03
    result.p95_latency_seconds = 520.0

    failures: list[str] = []
    if result.top3_hit_rate < THRESHOLDS["top3"]:
        failures.append("top3_hit_rate below threshold")
    if result.top1_hit_rate < THRESHOLDS["top1"]:
        failures.append("top1_hit_rate below threshold")
    if result.unsupported_claim_rate >= THRESHOLDS["unsupported"]:
        failures.append("unsupported_claim_rate above threshold")

    result.gate_passed = not failures
    result.failures = failures
    result.ended_at = datetime.now(timezone.utc)

    store.eval_runs[run_id] = result
    return result


@app.get("/v1/evals/runs/{run_id}")
def get_eval_run(run_id: str) -> EvalRunResult:
    run = store.eval_runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="eval run not found")
    return run


@app.post("/v1/evals/adjudications")
def post_adjudication(record: AdjudicationRecord) -> dict[str, str]:
    store.adjudications.append(record)
    return {"status": "recorded", "investigation_id": record.investigation_id}
