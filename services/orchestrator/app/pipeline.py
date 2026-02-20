from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from connectors.core.azure.plugin import AzureConnector
from connectors.core.newrelic.plugin import NewRelicConnector
from connectors.core.otel.plugin import OTelConnector
from platform_core.connector_runtime import ConnectorRuntime
from platform_core.evidence_store import EvidenceStore
from platform_core.llm_router import ModelRoute, synthesize_with_fallback
from platform_core.models import AlertEnvelope, EvidenceItem, Hypothesis, InvestigationPlan, RcaReport, ServiceIdentity
from platform_core.planner import build_default_plan
from platform_core.policy import enforce_budget_policy, enforce_citation_policy
from platform_core.policy_service import PolicyService
from platform_core.publisher import Publisher
from platform_core.resolver import resolve_service_identity


def resolve_service_stage(alert_payload: dict[str, Any]) -> dict[str, Any]:
    alert = AlertEnvelope.model_validate(alert_payload)
    entities = list(dict.fromkeys(alert.entity_ids))
    nr_candidates = entities if alert.source == "newrelic" else []
    azure_candidates = entities
    cmdb_candidates = [f"{entity}-cmdb" for entity in entities[:1]]
    rag_candidates = [f"{entity}-rag" for entity in entities[:1]]

    identity = resolve_service_identity(
        alert,
        nr_candidates=nr_candidates,
        azure_candidates=azure_candidates,
        cmdb_candidates=cmdb_candidates,
        rag_candidates=rag_candidates,
    )
    return identity.model_dump(mode="json")


def build_plan_stage(investigation_id: str, alert_payload: dict[str, Any]) -> dict[str, Any]:
    alert = AlertEnvelope.model_validate(alert_payload)
    plan = build_default_plan(investigation_id=investigation_id, alert=alert)
    enforce_budget_policy(plan)
    return plan.model_dump(mode="json")


def collect_evidence_stage(
    investigation_id: str,
    alert_payload: dict[str, Any],
    plan_payload: dict[str, Any],
    early_stop_min_citations: int = 3,
) -> dict[str, Any]:
    alert = AlertEnvelope.model_validate(alert_payload)
    plan = InvestigationPlan.model_validate(plan_payload)

    connectors = [NewRelicConnector(), AzureConnector(), OTelConnector()]
    policy_service = PolicyService()
    for connector in connectors:
        policy_service.validate_connector(connector)

    runtime = ConnectorRuntime(connectors)
    evidence_store = EvidenceStore()
    provider_signal_counts: defaultdict[str, int] = defaultdict(int)

    timeline: list[str] = []
    executed_steps = 0
    stopped_early = False

    for step in plan.ordered_steps:
        executed_steps += 1
        step_payload = step.model_dump(mode="json")
        step_payload["entity_ids"] = alert.entity_ids
        step_payload["incident_key"] = alert.incident_key

        signals = runtime.route_collect(step.provider, step.capability, step_payload)
        timeline.append(f"Collected {len(signals)} signal(s) from {step.provider}/{step.capability}")

        for signal in signals:
            evidence = evidence_store.add(
                investigation_id=investigation_id,
                provider=step.provider,
                evidence_type=step.capability,
                payload=signal,
            )
            provider_signal_counts[step.provider] += 1
            policy_service.validate_redaction_state(evidence.redaction_state)

        evidence_items = evidence_store.list(investigation_id)
        if len(evidence_items) >= early_stop_min_citations:
            top_provider_hits = max(provider_signal_counts.values(), default=0)
            tied_top = sum(1 for count in provider_signal_counts.values() if count == top_provider_hits) > 1
            if top_provider_hits >= 2 and not tied_top:
                stopped_early = True
                timeline.append("Early stop: confidence threshold reached without conflicting top signals.")
                break

    evidence_items = evidence_store.list(investigation_id)
    if not evidence_items:
        fallback_evidence = evidence_store.add(
            investigation_id=investigation_id,
            provider=alert.source,
            evidence_type="alert-context",
            payload={"incident_key": alert.incident_key, "raw_payload_ref": alert.raw_payload_ref},
        )
        policy_service.validate_redaction_state(fallback_evidence.redaction_state)
        evidence_items = [fallback_evidence]
        timeline.append("No connector signals found; used alert-context fallback evidence.")

    return {
        "executed_steps": executed_steps,
        "stopped_early": stopped_early,
        "timeline": timeline,
        "evidence": [item.model_dump(mode="json") for item in evidence_items],
    }


def synthesize_report_stage(
    alert_payload: dict[str, Any],
    service_identity_payload: dict[str, Any],
    evidence_payload: list[dict[str, Any]],
) -> dict[str, Any]:
    _ = AlertEnvelope.model_validate(alert_payload)
    service_identity = ServiceIdentity.model_validate(service_identity_payload)
    evidence_items = [EvidenceItem.model_validate(item) for item in evidence_payload]

    citations_by_provider: defaultdict[str, list[str]] = defaultdict(list)
    for item in evidence_items:
        citations_by_provider[item.provider].append(item.citation_id)

    provider_rank = sorted(citations_by_provider.items(), key=lambda kv: len(kv[1]), reverse=True)
    templates = {
        "newrelic": "Application metrics indicate an error-rate regression in the target service.",
        "azure": "Infrastructure events in Azure likely impacted dependent service health.",
        "otel": "Trace/log anomalies show a dependency latency bottleneck in the request path.",
    }

    hypotheses: list[Hypothesis] = []
    for idx, (provider, citations) in enumerate(provider_rank[:3]):
        statement = templates.get(provider, "Correlated evidence indicates a service-level degradation pattern.")
        hypotheses.append(
            Hypothesis(
                statement=statement,
                confidence=max(0.45, 0.82 - (idx * 0.14)),
                supporting_citations=citations[:3],
                counter_evidence_citations=[],
            )
        )

    enforce_citation_policy(hypotheses)

    route = ModelRoute(
        primary=os.getenv("PRIMARY_LLM_MODEL", "codex"),
        fallback=os.getenv("FALLBACK_LLM_MODEL", "claude"),
    )
    prompt = (
        f"Synthesize RCA for service {service_identity.canonical_service_id} "
        f"using {len(evidence_items)} evidence item(s)."
    )

    def primary_call(model: str, prompt_text: str) -> str:
        if os.getenv("SIMULATE_PRIMARY_LLM_FAILURE") == "1":
            raise RuntimeError("simulated primary failure")
        return f"{model}: {prompt_text}"

    def fallback_call(model: str, prompt_text: str) -> str:
        return f"{model}: fallback synthesis for {prompt_text}"

    model_used, llm_summary = synthesize_with_fallback(route, primary_call, fallback_call, prompt)

    report = RcaReport(
        top_hypotheses=hypotheses,
        likely_cause=hypotheses[0].statement,
        blast_radius=f"Primary impact on {service_identity.canonical_service_id} and direct dependencies.",
        recommended_manual_actions=[
            "Validate recent deploys/config changes for impacted service and dependencies.",
            "Correlate logs/traces around incident window and confirm rollback criteria.",
            "Escalate to service owner for manual mitigation if customer impact persists.",
        ],
        confidence=round(sum(h.confidence for h in hypotheses) / len(hypotheses), 2),
    )

    return {
        "report": report.model_dump(mode="json"),
        "hypotheses": [h.model_dump(mode="json") for h in hypotheses],
        "llm_model_used": model_used,
        "llm_summary": llm_summary,
    }


def publish_stage(alert_payload: dict[str, Any], report_payload: dict[str, Any], enabled: bool = True) -> dict[str, Any]:
    alert = AlertEnvelope.model_validate(alert_payload)
    report = RcaReport.model_validate(report_payload)

    if not enabled:
        return {"published": False, "slack_message_id": None, "jira_issue_key": None}

    publish_result = Publisher().publish(report=report, incident_key=alert.incident_key)
    return {
        "published": True,
        "slack_message_id": publish_result.slack_message_id,
        "jira_issue_key": publish_result.jira_issue_key,
    }


def emit_eval_event_stage(
    investigation_id: str,
    report_payload: dict[str, Any],
    evidence_payload: list[dict[str, Any]],
    latency_seconds: float,
) -> dict[str, Any]:
    report = RcaReport.model_validate(report_payload)
    evidence = [EvidenceItem.model_validate(item) for item in evidence_payload]
    return {
        "investigation_id": investigation_id,
        "top_hypothesis_count": len(report.top_hypotheses),
        "citation_count": sum(len(h.supporting_citations) for h in report.top_hypotheses),
        "evidence_count": len(evidence),
        "latency_seconds": round(latency_seconds, 2),
        "requires_human_review": True,
        "required_human_review_percent": 100,
        "rollout_mode": "shadow",
        "emitted_at": datetime.now(timezone.utc).isoformat(),
    }
