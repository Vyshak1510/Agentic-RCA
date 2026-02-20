from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from platform_core.models import Hypothesis, RcaReport
from platform_core.policy import PolicyError, enforce_citation_policy

app = FastAPI(title="rca-analysis-engine", version="0.1.0")


class SynthesisRequest(BaseModel):
    hypotheses: list[Hypothesis]
    blast_radius: str
    recommended_manual_actions: list[str]


@app.get("/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "analysis-engine"}


@app.post("/v1/synthesize")
def synthesize(req: SynthesisRequest) -> RcaReport:
    top = sorted(req.hypotheses, key=lambda x: x.confidence, reverse=True)[:3]
    try:
        enforce_citation_policy(top)
    except PolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    likely = top[0].statement
    confidence = sum(h.confidence for h in top) / len(top)
    return RcaReport(
        top_hypotheses=top,
        likely_cause=likely,
        blast_radius=req.blast_radius,
        recommended_manual_actions=req.recommended_manual_actions,
        confidence=confidence,
    )
