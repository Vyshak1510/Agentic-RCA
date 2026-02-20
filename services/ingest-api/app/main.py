from __future__ import annotations

from datetime import datetime, timezone
from os import getenv
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from platform_core.models import (
    AlertEnvelope,
    InvestigationRecord,
    InvestigationStatus,
    LlmProviderRoute,
    MappingUpsertRequest,
)
from platform_core.planner import build_default_plan
from platform_core.policy import enforce_budget_policy
from platform_core.store import store

app = FastAPI(title="rca-ingest-api", version="0.1.0")


class NewRelicWebhook(BaseModel):
    policy_name: str
    condition_name: str
    incident_id: str
    severity: str = "critical"
    entities: list[str] = []
    timestamp: datetime
    payload: dict = {}


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    configured = getenv("API_KEY")
    if configured and x_api_key != configured:
        raise HTTPException(status_code=401, detail="invalid api key")


@app.get("/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ingest-api"}


@app.get("/v1/metrics")
def metrics() -> dict[str, int]:
    return {"alerts_ingested_total": store.counters["alerts_ingested_total"]}


@app.post("/v1/alerts")
def ingest_alert(alert: AlertEnvelope, _: None = Depends(require_api_key)) -> dict[str, str | bool]:
    investigation_id = str(uuid4())
    now = datetime.now(timezone.utc)
    plan = build_default_plan(investigation_id=investigation_id, alert=alert)
    enforce_budget_policy(plan)
    record = InvestigationRecord(
        id=investigation_id,
        status=InvestigationStatus.PENDING,
        created_at=now,
        updated_at=now,
        alert=alert,
        plan=plan,
        timeline=["Alert accepted", "Investigation plan created"],
    )
    saved_id, deduped = store.record_alert(record)
    if deduped:
        return {"investigation_id": saved_id, "deduped": True}
    return {"investigation_id": investigation_id, "deduped": False}


@app.post("/v1/alerts/newrelic")
def ingest_newrelic_alert(payload: NewRelicWebhook, _: None = Depends(require_api_key)) -> dict[str, str | bool]:
    envelope = AlertEnvelope(
        source="newrelic",
        severity=payload.severity,
        incident_key=payload.incident_id,
        entity_ids=payload.entities,
        timestamps={"triggered_at": payload.timestamp},
        raw_payload_ref=f"newrelic://{payload.incident_id}",
        raw_payload=payload.payload,
    )
    return ingest_alert(envelope)


@app.get("/v1/investigations/{investigation_id}")
def get_investigation(investigation_id: str, _: None = Depends(require_api_key)) -> InvestigationRecord:
    investigation = store.get_investigation(investigation_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="investigation not found")
    return investigation


@app.post("/v1/investigations/{investigation_id}/rerun")
def rerun_investigation(investigation_id: str, _: None = Depends(require_api_key)) -> dict[str, str]:
    investigation = store.update_status(investigation_id, InvestigationStatus.PENDING)
    if not investigation:
        raise HTTPException(status_code=404, detail="investigation not found")
    investigation.timeline.append("Rerun requested")
    return {"investigation_id": investigation_id, "status": investigation.status.value}


@app.post("/v1/catalog/mappings/upsert")
def upsert_mapping(mapping: MappingUpsertRequest, _: None = Depends(require_api_key)) -> dict[str, str]:
    key = (mapping.provider, mapping.provider_entity_id)
    store.mappings[key] = mapping
    return {"status": "ok", "canonical_service_id": mapping.canonical_service_id}


@app.post("/v1/providers/llm")
def upsert_llm_route(route: LlmProviderRoute, _: None = Depends(require_api_key)) -> dict[str, str]:
    key = (route.tenant, route.environment)
    store.llm_routes[key] = route
    return {"status": "ok", "tenant": route.tenant, "environment": route.environment}
