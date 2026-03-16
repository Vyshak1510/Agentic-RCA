from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import json
import re
from typing import Any

from platform_core.models import (
    AlertEnvelope,
    AliasCandidateScore,
    AliasDecisionTrace,
    ArtifactState,
    ArtifactUpdate,
    McpExecutionPhase,
    McpExecutionProfile,
    McpScopeKind,
    McpToolDescriptor,
    ResolvedTelemetryAlias,
)


@dataclass(frozen=True)
class ToolSelectionDiagnostics:
    invocable: bool
    missing_artifacts: list[str]
    reasons: list[str]


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _tokenize(value: str) -> list[str]:
    tokens = [token for token in re.split(r"[^a-zA-Z0-9]+", value.lower()) if token]
    return tokens


_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "are",
    "was",
    "were",
    "into",
    "than",
    "then",
    "when",
    "where",
    "what",
    "which",
    "service",
    "customer",
    "facing",
    "requests",
    "served",
    "slower",
    "normal",
    "production",
    "appears",
    "degraded",
    "investigate",
    "determine",
    "likely",
    "root",
    "cause",
    "latency",
    "resource",
    "usage",
    "abnormal",
    "critical",
}


def _unique(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    return deduped


def _service_like_terms(value: str) -> list[str]:
    tokens = [token for token in _tokenize(value) if token not in _STOPWORDS and len(token) > 2]
    derived: list[str] = []
    derived.extend(tokens)
    for left, right in zip(tokens, tokens[1:]):
        derived.append(f"{left}{right}")
    return _unique(derived)


def _meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, set, tuple)):
        return bool(value)
    return True


def _profile(
    server_id: str,
    tool_name: str,
    *,
    phase: McpExecutionPhase,
    scope_kind: McpScopeKind,
    requires_artifacts: list[str] | None = None,
    produces_artifacts: list[str] | None = None,
    default_priority: int = 100,
    result_adapter: str | None = None,
) -> McpExecutionProfile:
    return McpExecutionProfile(
        server_id=server_id,
        tool_name=tool_name,
        phase=phase,
        scope_kind=scope_kind,
        requires_artifacts=requires_artifacts or [],
        produces_artifacts=produces_artifacts or [],
        default_priority=default_priority,
        result_adapter=result_adapter,
    )


_EXECUTION_PROFILES: dict[tuple[str, str], McpExecutionProfile] = {
    ("jaeger", "get_services"): _profile(
        "jaeger",
        "get_services",
        phase=McpExecutionPhase.DISCOVER,
        scope_kind=McpScopeKind.GLOBAL,
        produces_artifacts=["service_candidates"],
        default_priority=0,
        result_adapter="jaeger_services",
    ),
    ("jaeger", "service_operations"): _profile(
        "jaeger",
        "service_operations",
        phase=McpExecutionPhase.INSPECT,
        scope_kind=McpScopeKind.SERVICE,
        requires_artifacts=["resolved_service"],
        produces_artifacts=["operation_candidates"],
        default_priority=10,
        result_adapter="jaeger_operations",
    ),
    ("jaeger", "get_operations"): _profile(
        "jaeger",
        "get_operations",
        phase=McpExecutionPhase.INSPECT,
        scope_kind=McpScopeKind.SERVICE,
        requires_artifacts=["resolved_service"],
        produces_artifacts=["operation_candidates"],
        default_priority=10,
        result_adapter="jaeger_operations",
    ),
    ("jaeger", "find_error_traces"): _profile(
        "jaeger",
        "find_error_traces",
        phase=McpExecutionPhase.INSPECT,
        scope_kind=McpScopeKind.SERVICE,
        requires_artifacts=["resolved_service"],
        produces_artifacts=["trace_ids", "trace_summaries", "root_cause_signals"],
        default_priority=20,
        result_adapter="jaeger_traces",
    ),
    ("jaeger", "search_traces"): _profile(
        "jaeger",
        "search_traces",
        phase=McpExecutionPhase.INSPECT,
        scope_kind=McpScopeKind.SERVICE,
        requires_artifacts=["resolved_service"],
        produces_artifacts=["trace_ids", "trace_summaries", "root_cause_signals"],
        default_priority=20,
        result_adapter="jaeger_traces",
    ),
    ("jaeger", "get_trace"): _profile(
        "jaeger",
        "get_trace",
        phase=McpExecutionPhase.DRILLDOWN,
        scope_kind=McpScopeKind.TRACE,
        requires_artifacts=["trace_ids"],
        produces_artifacts=["dependency_edges", "root_cause_signals", "service_candidates"],
        default_priority=30,
        result_adapter="jaeger_trace_detail",
    ),
    ("grafana", "list_datasources"): _profile(
        "grafana",
        "list_datasources",
        phase=McpExecutionPhase.DISCOVER,
        scope_kind=McpScopeKind.GLOBAL,
        produces_artifacts=["datasource_ids"],
        default_priority=0,
        result_adapter="grafana_datasources",
    ),
    ("grafana", "search_dashboards"): _profile(
        "grafana",
        "search_dashboards",
        phase=McpExecutionPhase.DISCOVER,
        scope_kind=McpScopeKind.GLOBAL,
        produces_artifacts=["dashboard_uids"],
        default_priority=5,
        result_adapter="grafana_dashboards",
    ),
    ("grafana", "get_annotation_tags"): _profile(
        "grafana",
        "get_annotation_tags",
        phase=McpExecutionPhase.DISCOVER,
        scope_kind=McpScopeKind.GLOBAL,
        produces_artifacts=["annotation_tags"],
        default_priority=8,
        result_adapter="grafana_annotation_tags",
    ),
    ("grafana", "get_annotations"): _profile(
        "grafana",
        "get_annotations",
        phase=McpExecutionPhase.INSPECT,
        scope_kind=McpScopeKind.GLOBAL,
        produces_artifacts=["annotation_tags", "root_cause_signals"],
        default_priority=12,
        result_adapter="grafana_annotations",
    ),
    ("grafana", "list_alert_rules"): _profile(
        "grafana",
        "list_alert_rules",
        phase=McpExecutionPhase.DISCOVER,
        scope_kind=McpScopeKind.GLOBAL,
        produces_artifacts=["root_cause_signals"],
        default_priority=14,
        result_adapter="grafana_alert_rules",
    ),
    ("grafana", "list_contact_points"): _profile(
        "grafana",
        "list_contact_points",
        phase=McpExecutionPhase.DISCOVER,
        scope_kind=McpScopeKind.GLOBAL,
        default_priority=90,
        result_adapter="noop",
    ),
    ("prometheus", "list_label_names"): _profile(
        "prometheus",
        "list_label_names",
        phase=McpExecutionPhase.DISCOVER,
        scope_kind=McpScopeKind.GLOBAL,
        produces_artifacts=["metric_label_keys"],
        default_priority=0,
        result_adapter="prometheus_label_names",
    ),
    ("prometheus", "list_label_values"): _profile(
        "prometheus",
        "list_label_values",
        phase=McpExecutionPhase.DISCOVER,
        scope_kind=McpScopeKind.METRIC,
        produces_artifacts=["metric_service_candidates"],
        default_priority=5,
        result_adapter="prometheus_label_values",
    ),
    ("prometheus", "query_instant"): _profile(
        "prometheus",
        "query_instant",
        phase=McpExecutionPhase.INSPECT,
        scope_kind=McpScopeKind.METRIC,
        requires_artifacts=["resolved_service"],
        produces_artifacts=["root_cause_signals"],
        default_priority=20,
        result_adapter="prometheus_query",
    ),
    ("prometheus", "query_range"): _profile(
        "prometheus",
        "query_range",
        phase=McpExecutionPhase.INSPECT,
        scope_kind=McpScopeKind.METRIC,
        requires_artifacts=["resolved_service"],
        produces_artifacts=["root_cause_signals"],
        default_priority=22,
        result_adapter="prometheus_query",
    ),
}


def execution_profile_for_tool(descriptor: McpToolDescriptor) -> McpExecutionProfile:
    key = (descriptor.server_id.lower(), descriptor.tool_name.lower())
    profile = _EXECUTION_PROFILES.get(key)
    if profile:
        return profile

    if descriptor.required_args and any(_norm(arg) in {"traceid", "trace_id"} for arg in descriptor.required_args):
        phase = McpExecutionPhase.DRILLDOWN
        scope = McpScopeKind.TRACE
        requires_artifacts = ["trace_ids"]
    elif descriptor.required_args and any(_norm(arg) in {"service", "servicename", "service_name"} for arg in descriptor.required_args):
        phase = McpExecutionPhase.INSPECT
        scope = McpScopeKind.SERVICE
        requires_artifacts = ["resolved_service"]
    else:
        phase = McpExecutionPhase.DISCOVER
        scope = McpScopeKind.GLOBAL
        requires_artifacts = []

    return _profile(
        descriptor.server_id,
        descriptor.tool_name,
        phase=phase,
        scope_kind=scope,
        requires_artifacts=requires_artifacts,
        produces_artifacts=[],
        default_priority=100,
        result_adapter=None,
    )


def enrich_tool_descriptor(descriptor: McpToolDescriptor) -> McpToolDescriptor:
    profile = execution_profile_for_tool(descriptor)
    return descriptor.model_copy(
        update={
            "phase": profile.phase,
            "scope_kind": profile.scope_kind,
            "requires_artifacts": profile.requires_artifacts,
            "produces_artifacts": profile.produces_artifacts,
            "default_priority": profile.default_priority,
            "result_adapter": profile.result_adapter,
        }
    )


def enrich_tool_descriptors(descriptors: list[McpToolDescriptor]) -> list[McpToolDescriptor]:
    return [enrich_tool_descriptor(item) for item in descriptors]


def seed_artifact_state(
    alert_payload: dict[str, Any],
    service_identity: dict[str, Any] | None = None,
) -> ArtifactState:
    alert = AlertEnvelope.model_validate(alert_payload)
    entity_terms = _unique(alert.entity_ids)
    explicit_service_terms: list[str] = []
    title_terms: list[str] = []
    summary_terms: list[str] = []
    terms: list[str] = [*entity_terms, alert.incident_key]

    if isinstance(alert.raw_payload, dict):
        title_value = alert.raw_payload.get("title")
        if isinstance(title_value, str) and title_value.strip():
            title_terms.extend(_service_like_terms(title_value))
            terms.append(title_value)

        for key in ("service", "service_name", "serviceName", "entity", "component"):
            value = alert.raw_payload.get(key)
            if isinstance(value, str) and value.strip():
                explicit_service_terms.append(value.strip())
                terms.append(value.strip())
            elif isinstance(value, list):
                entries = [item.strip() for item in value if isinstance(item, str) and item.strip()]
                explicit_service_terms.extend(entries)
                terms.extend(entries)

        for key in ("summary", "description", "workflowName"):
            value = alert.raw_payload.get(key)
            if isinstance(value, str) and value.strip():
                summary_terms.extend(_service_like_terms(value))
                terms.append(value.strip())
            elif isinstance(value, list):
                entries = [item.strip() for item in value if isinstance(item, str) and item.strip()]
                for entry in entries:
                    summary_terms.extend(_service_like_terms(entry))
                terms.extend(entries)

    resolved_service = None
    aliases: list[ResolvedTelemetryAlias] = []
    if isinstance(service_identity, dict):
        resolved_service = str(service_identity.get("canonical_service_id") or "").strip() or None
        if resolved_service:
            aliases.append(
                ResolvedTelemetryAlias(
                    alert_term=resolved_service,
                    resolved_value=resolved_service,
                    source="service_identity",
                    confidence=float(service_identity.get("confidence") or 0.9),
                    candidates=[],
                )
            )

    return ArtifactState(
        alert_terms=_unique(terms),
        entity_terms=entity_terms,
        explicit_service_terms=_unique(explicit_service_terms),
        title_terms=_unique(title_terms),
        summary_terms=_unique(summary_terms),
        resolved_service=resolved_service,
        service_aliases=aliases,
        resolved_operations=[],
    )


def artifact_state_to_context(
    artifact_state: ArtifactState,
    alert_payload: dict[str, Any] | None = None,
    service_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    if alert_payload is not None:
        alert = AlertEnvelope.model_validate(alert_payload)
        context.update(
            {
                "source": alert.source,
                "severity": alert.severity,
                "incident_key": alert.incident_key,
                "entity_ids": alert.entity_ids,
            }
        )
        if alert.entity_ids:
            context["entity_id"] = alert.entity_ids[0]
            context["entity"] = alert.entity_ids[0]
        for key, value in (alert.timestamps or {}).items():
            if value is None:
                continue
            context[key] = value.isoformat() if hasattr(value, "isoformat") else str(value)

    if isinstance(service_identity, dict):
        canonical = str(service_identity.get("canonical_service_id") or "").strip()
        if canonical:
            context["canonical_service_id"] = canonical

    resolved_service = artifact_state.resolved_service or context.get("canonical_service_id")
    if resolved_service:
        context["service"] = resolved_service
        context["service_name"] = resolved_service
        context["serviceName"] = resolved_service
        context["canonical_service_id"] = resolved_service

    if artifact_state.resolved_operations:
        context["operation"] = artifact_state.resolved_operations[0]
        context["resolved_operation"] = artifact_state.resolved_operations[0]
    if artifact_state.trace_ids:
        context["trace_id"] = artifact_state.trace_ids[0]
    if artifact_state.dashboard_uids:
        context["dashboard_uid"] = artifact_state.dashboard_uids[0]
        context["uid"] = artifact_state.dashboard_uids[0]
    if artifact_state.datasource_ids:
        context.setdefault("uid", artifact_state.datasource_ids[0])
        context["datasource_uid"] = artifact_state.datasource_ids[0]
    if artifact_state.annotation_tags:
        context["tag"] = artifact_state.annotation_tags[0]
        context["tags"] = artifact_state.annotation_tags
    if artifact_state.metric_label_keys:
        context["metric_label_keys"] = artifact_state.metric_label_keys

    return context


def _score_service_match(term: str, candidate: str) -> float:
    norm_term = _norm(term)
    norm_candidate = _norm(candidate)
    if not norm_term or not norm_candidate:
        return 0.0
    if norm_term == norm_candidate:
        return 1.0
    if norm_term in norm_candidate or norm_candidate in norm_term:
        return 0.94

    stripped_term = norm_term.removesuffix("service")
    stripped_candidate = norm_candidate.removesuffix("service")
    if stripped_term and stripped_term == stripped_candidate:
        return 0.91
    if stripped_term and stripped_term in stripped_candidate:
        return 0.88
    if stripped_candidate and stripped_candidate in stripped_term:
        return 0.88

    token_overlap = set(_tokenize(term)) & set(_tokenize(candidate))
    overlap_score = min(len(token_overlap) * 0.15, 0.45)
    ratio = SequenceMatcher(None, norm_term, norm_candidate).ratio()
    return min(0.15 + overlap_score + ratio * 0.55, 0.89)


def _source_terms(artifact_state: ArtifactState) -> list[tuple[str, list[str], float]]:
    return [
        ("entity_ids", artifact_state.entity_terms, 0.72),
        ("explicit_service", artifact_state.explicit_service_terms, 0.72),
        ("title", artifact_state.title_terms, 0.62),
        ("summary", artifact_state.summary_terms, 0.55),
    ]


def _candidate_scores(terms: list[str], source: str, candidates: list[str]) -> list[AliasCandidateScore]:
    scored: list[AliasCandidateScore] = []
    for term in terms:
        if not term.strip():
            continue
        for candidate in candidates:
            scored.append(
                AliasCandidateScore(
                    term=term,
                    term_source=source,
                    candidate=candidate,
                    score=_score_service_match(term, candidate),
                )
            )
    scored.sort(key=lambda item: (-item.score, len(item.candidate), item.candidate))
    return scored


def _break_tie(
    candidates: list[str],
    artifact_state: ArtifactState,
    source_order: list[tuple[str, list[str], float]],
    starting_index: int,
) -> tuple[str | None, float]:
    if len(candidates) == 1:
        return candidates[0], 0.0

    bonus_scores = {candidate: 0.0 for candidate in candidates}
    for source, terms, _ in source_order[starting_index + 1 :]:
        scored = _candidate_scores(terms, source, candidates)
        if not scored:
            continue
        per_candidate: dict[str, float] = {}
        for item in scored:
            per_candidate[item.candidate] = max(per_candidate.get(item.candidate, 0.0), item.score)
        for candidate, score in per_candidate.items():
            weight = 0.1 if source == "summary" else 0.14
            bonus_scores[candidate] += score * weight

    ranked = sorted(bonus_scores.items(), key=lambda item: (-item[1], len(item[0]), item[0]))
    if not ranked:
        return None, 0.0
    if len(ranked) == 1:
        return ranked[0][0], ranked[0][1]
    best_candidate, best_bonus = ranked[0]
    second_bonus = ranked[1][1]
    if best_bonus - second_bonus < 0.03:
        return None, best_bonus
    return best_candidate, best_bonus


def resolve_service_aliases(artifact_state: ArtifactState) -> tuple[ArtifactState, list[ResolvedTelemetryAlias]]:
    candidates = _unique([*artifact_state.service_candidates, *artifact_state.metric_service_candidates])
    if not candidates:
        updated = artifact_state.model_copy(
            update={
                "alias_decision_trace": AliasDecisionTrace(
                    selected_candidate=artifact_state.resolved_service,
                    matched_term=None,
                    matched_term_source=None,
                    confidence=0.0,
                    ambiguous_candidates=[],
                    top_candidates=[],
                    unresolved_reason="no_service_candidates",
                )
            }
        )
        return updated, updated.service_aliases

    source_order = _source_terms(artifact_state)
    top_candidates: list[AliasCandidateScore] = []
    chosen: AliasCandidateScore | None = None
    unresolved_reason: str | None = None
    ambiguous_candidates: list[str] = []

    for index, (source, terms, threshold) in enumerate(source_order):
        scored = _candidate_scores(terms, source, candidates)
        if not scored:
            continue
        for item in scored[:6]:
            if all(
                not (
                    existing.candidate == item.candidate
                    and existing.term_source == item.term_source
                    and existing.term == item.term
                )
                for existing in top_candidates
            ):
                top_candidates.append(item)
        per_candidate: dict[str, AliasCandidateScore] = {}
        for item in scored:
            current = per_candidate.get(item.candidate)
            if current is None or item.score > current.score:
                per_candidate[item.candidate] = item
        ranked = sorted(per_candidate.values(), key=lambda item: (-item.score, len(item.candidate), item.candidate))
        if not ranked or ranked[0].score < threshold:
            continue
        best = ranked[0]
        ambiguous_candidates = [
            item.candidate
            for item in ranked
            if item.score >= threshold and (best.score - item.score) <= 0.03
        ]
        if len(ambiguous_candidates) > 1:
            tie_choice, tie_bonus = _break_tie(ambiguous_candidates, artifact_state, source_order, index)
            if tie_choice is None:
                unresolved_reason = f"ambiguous_{source}"
                break
            for item in ranked:
                if item.candidate == tie_choice:
                    chosen = item.model_copy(update={"score": min(0.99, item.score + tie_bonus)})
                    break
        else:
            chosen = best
        if chosen is not None:
            break

    if chosen is None:
        updated = artifact_state.model_copy(
            update={
                "resolved_service": artifact_state.resolved_service,
                "service_candidates": candidates,
                "alias_decision_trace": AliasDecisionTrace(
                    selected_candidate=artifact_state.resolved_service,
                    matched_term=None,
                    matched_term_source=None,
                    confidence=0.0,
                    ambiguous_candidates=ambiguous_candidates[:4],
                    top_candidates=top_candidates[:8],
                    unresolved_reason=unresolved_reason or "no_anchored_candidate",
                ),
            }
        )
        return updated, updated.service_aliases

    alias = ResolvedTelemetryAlias(
        alert_term=chosen.term,
        resolved_value=chosen.candidate,
        source=chosen.term_source,
        confidence=chosen.score,
        candidates=candidates[:6],
    )

    aliases = [entry for entry in artifact_state.service_aliases if _norm(entry.resolved_value) != _norm(chosen.candidate)]
    aliases.append(alias)
    updated = artifact_state.model_copy(
        update={
            "resolved_service": chosen.candidate,
            "service_aliases": aliases,
            "service_candidates": candidates,
            "alias_decision_trace": AliasDecisionTrace(
                selected_candidate=chosen.candidate,
                matched_term=chosen.term,
                matched_term_source=chosen.term_source,
                confidence=chosen.score,
                ambiguous_candidates=[item for item in ambiguous_candidates if item != chosen.candidate][:4],
                top_candidates=top_candidates[:8],
                unresolved_reason=None,
            ),
        }
    )
    return updated, aliases


def tool_diagnostics(descriptor: McpToolDescriptor, artifact_state: ArtifactState) -> ToolSelectionDiagnostics:
    missing = [token for token in descriptor.requires_artifacts if not _meaningful(getattr(artifact_state, token, None))]
    invocable = not missing
    reasons = [] if invocable else [f"missing_artifact:{item}" for item in missing]
    return ToolSelectionDiagnostics(invocable=invocable, missing_artifacts=missing, reasons=reasons)


def _parse_list_result(result: dict[str, Any]) -> list[Any]:
    if isinstance(result.get("result"), list):
        return result["result"]
    if isinstance(result.get("data"), list):
        return result["data"]
    if isinstance(result.get("items"), list):
        return result["items"]
    content = result.get("content")
    if isinstance(content, list):
        extracted: list[Any] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip().startswith(("[", "{")):
                    try:
                        parsed = json.loads(text)
                    except Exception:
                        continue
                    if isinstance(parsed, list):
                        extracted.extend(parsed)
                    else:
                        extracted.append(parsed)
                else:
                    extracted.append(item)
        if extracted:
            return extracted
    return []


def _extract_strings(payload: Any) -> list[str]:
    if isinstance(payload, str):
        text = payload.strip()
        return [text] if text else []
    if isinstance(payload, dict):
        values: list[str] = []
        for key in ("service", "serviceName", "service_name", "name", "value", "uid", "title", "tag"):
            raw = payload.get(key)
            if isinstance(raw, str) and raw.strip():
                values.append(raw.strip())
        return values
    if isinstance(payload, list):
        values: list[str] = []
        for item in payload:
            values.extend(_extract_strings(item))
        return values
    return []


def extract_artifact_update(descriptor: McpToolDescriptor, result: dict[str, Any]) -> ArtifactUpdate:
    adapter = descriptor.result_adapter or execution_profile_for_tool(descriptor).result_adapter or "noop"
    fqdn = f"mcp.{descriptor.server_id}.{descriptor.tool_name}"
    items = _parse_list_result(result)

    if adapter == "jaeger_services":
        candidates = _unique(_extract_strings(items))
        return ArtifactUpdate(source_tool=fqdn, service_candidates=candidates)

    if adapter == "jaeger_operations":
        operations = []
        for item in items:
            if isinstance(item, dict):
                name = item.get("name") or item.get("operation") or item.get("value")
                if isinstance(name, str):
                    operations.append(name)
            elif isinstance(item, str):
                operations.append(item)
        return ArtifactUpdate(source_tool=fqdn, operation_candidates=_unique(operations))

    if adapter == "jaeger_traces":
        trace_ids: list[str] = []
        trace_summaries: list[dict[str, Any]] = []
        service_candidates: list[str] = []
        root_cause_signals: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            trace_id = item.get("traceID") or item.get("trace_id")
            if isinstance(trace_id, str) and trace_id.strip():
                trace_ids.append(trace_id.strip())
            root_service = item.get("rootService") or item.get("service")
            if isinstance(root_service, str) and root_service.strip():
                service_candidates.append(root_service.strip())
            summary = {
                key: item.get(key)
                for key in ("traceID", "rootSpan", "rootService", "duration_us", "errors", "startTime")
                if key in item
            }
            if summary:
                trace_summaries.append(summary)
            errors = item.get("errors")
            if isinstance(errors, int) and errors > 0:
                root_cause_signals.append(f"trace_errors:{errors}")
        return ArtifactUpdate(
            source_tool=fqdn,
            trace_ids=_unique(trace_ids),
            trace_summaries=trace_summaries[:12],
            service_candidates=_unique(service_candidates),
            root_cause_signals=_unique(root_cause_signals),
        )

    if adapter == "jaeger_trace_detail":
        dependency_edges: list[str] = []
        service_candidates: list[str] = []
        root_cause_signals: list[str] = []
        spans = [item for item in items if isinstance(item, dict)]
        for item in spans:
            service = item.get("service")
            operation = item.get("operationName")
            if isinstance(service, str) and service.strip():
                service_candidates.append(service.strip())
            if isinstance(service, str) and service.strip() and isinstance(operation, str) and operation.strip():
                dependency_edges.append(f"{service.strip()}::{operation.strip()}")
            if item.get("error") in (True, "true", "True"):
                signal = f"error_span:{service or 'unknown'}:{operation or 'unknown'}"
                root_cause_signals.append(signal)
        return ArtifactUpdate(
            source_tool=fqdn,
            dependency_edges=_unique(dependency_edges),
            root_cause_signals=_unique(root_cause_signals),
            service_candidates=_unique(service_candidates),
        )

    if adapter == "grafana_datasources":
        datasource_ids: list[str] = []
        for item in items:
            if isinstance(item, dict):
                for key in ("uid", "name"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        datasource_ids.append(value.strip())
        return ArtifactUpdate(source_tool=fqdn, datasource_ids=_unique(datasource_ids))

    if adapter == "grafana_dashboards":
        dashboard_uids: list[str] = []
        root_cause_signals: list[str] = []
        for item in items:
            if isinstance(item, dict):
                uid = item.get("uid") or item.get("dashboardUID") or item.get("dashboardUid")
                title = item.get("title")
                if isinstance(uid, str) and uid.strip():
                    dashboard_uids.append(uid.strip())
                if isinstance(title, str) and title.strip():
                    root_cause_signals.append(f"dashboard:{title.strip()}")
        return ArtifactUpdate(
            source_tool=fqdn,
            dashboard_uids=_unique(dashboard_uids),
            root_cause_signals=_unique(root_cause_signals),
        )

    if adapter == "grafana_annotation_tags":
        return ArtifactUpdate(source_tool=fqdn, annotation_tags=_unique(_extract_strings(items)))

    if adapter == "grafana_annotations":
        tags: list[str] = []
        root_cause_signals: list[str] = []
        for item in items:
            if isinstance(item, dict):
                item_tags = item.get("tags")
                if isinstance(item_tags, list):
                    tags.extend([str(tag) for tag in item_tags if str(tag).strip()])
                text = item.get("text") or item.get("title")
                if isinstance(text, str) and text.strip():
                    root_cause_signals.append(text.strip())
        return ArtifactUpdate(
            source_tool=fqdn,
            annotation_tags=_unique(tags),
            root_cause_signals=_unique(root_cause_signals),
        )

    if adapter == "grafana_alert_rules":
        signals: list[str] = []
        for item in items:
            if isinstance(item, dict):
                name = item.get("title") or item.get("name") or item.get("uid")
                if isinstance(name, str) and name.strip():
                    signals.append(f"alert_rule:{name.strip()}")
        return ArtifactUpdate(source_tool=fqdn, root_cause_signals=_unique(signals))

    if adapter == "prometheus_label_names":
        return ArtifactUpdate(source_tool=fqdn, metric_label_keys=_unique(_extract_strings(items)))

    if adapter == "prometheus_label_values":
        return ArtifactUpdate(source_tool=fqdn, metric_service_candidates=_unique(_extract_strings(items)))

    if adapter == "prometheus_query":
        signals: list[str] = []
        summaries: list[dict[str, Any]] = []
        for item in items[:12]:
            if isinstance(item, dict):
                metric = item.get("metric") if isinstance(item.get("metric"), dict) else {}
                values = item.get("values") if isinstance(item.get("values"), list) else []
                value = item.get("value") if isinstance(item.get("value"), list) else []
                summary = {
                    "metric": metric,
                    "samples": len(values) or (1 if value else 0),
                }
                summaries.append(summary)
                service_name = metric.get("service_name") or metric.get("service")
                if isinstance(service_name, str) and service_name.strip():
                    signals.append(f"metric_service:{service_name.strip()}")
                if values:
                    signals.append(f"timeseries:{len(values)}")
                elif value:
                    signals.append("instant_sample")
        return ArtifactUpdate(source_tool=fqdn, root_cause_signals=_unique(signals), trace_summaries=summaries)

    return ArtifactUpdate(source_tool=fqdn)


def merge_artifact_state(artifact_state: ArtifactState, update: ArtifactUpdate) -> ArtifactState:
    merged = artifact_state.model_copy(deep=True)
    merged.service_candidates = _unique([*merged.service_candidates, *update.service_candidates])
    merged.operation_candidates = _unique([*merged.operation_candidates, *update.operation_candidates])
    merged.trace_ids = _unique([*merged.trace_ids, *update.trace_ids])
    merged.datasource_ids = _unique([*merged.datasource_ids, *update.datasource_ids])
    merged.dashboard_uids = _unique([*merged.dashboard_uids, *update.dashboard_uids])
    merged.annotation_tags = _unique([*merged.annotation_tags, *update.annotation_tags])
    merged.metric_label_keys = _unique([*merged.metric_label_keys, *update.metric_label_keys])
    merged.metric_service_candidates = _unique([*merged.metric_service_candidates, *update.metric_service_candidates])
    merged.dependency_edges = _unique([*merged.dependency_edges, *update.dependency_edges])
    merged.root_cause_signals = _unique([*merged.root_cause_signals, *update.root_cause_signals])
    if update.trace_summaries:
        merged.trace_summaries = [*merged.trace_summaries, *update.trace_summaries][:24]
    if update.operation_candidates and not merged.resolved_operations:
        merged.resolved_operations = update.operation_candidates[:2]
    return merged


def invocable_tool_names(descriptors: list[McpToolDescriptor], artifact_state: ArtifactState) -> list[str]:
    names: list[str] = []
    for descriptor in descriptors:
        if tool_diagnostics(descriptor, artifact_state).invocable:
            names.append(f"mcp.{descriptor.server_id}.{descriptor.tool_name}")
    return names


def blocked_tool_entries(descriptors: list[McpToolDescriptor], artifact_state: ArtifactState) -> list[dict[str, Any]]:
    blocked: list[dict[str, Any]] = []
    for descriptor in descriptors:
        diagnostics = tool_diagnostics(descriptor, artifact_state)
        if diagnostics.invocable:
            continue
        blocked.append(
            {
                "tool_name": f"mcp.{descriptor.server_id}.{descriptor.tool_name}",
                "reason": "missing_artifacts",
                "missing_artifacts": diagnostics.missing_artifacts,
                "detail": ", ".join(diagnostics.reasons),
            }
        )
    return blocked


def default_prometheus_query(
    alert_terms: list[str],
    resolved_service: str,
    mode: str,
    *,
    scope: str = "service",
) -> str:
    joined = " ".join(alert_terms).lower()
    latency_like = any(token in joined for token in ("latency", "slow", "slowness", "duration", "timeout"))
    error_like = any(token in joined for token in ("error", "5xx", "exception", "fail"))
    service_filter = f'{{service_name="{resolved_service}"}}' if scope == "service" and resolved_service else ""
    error_filter = (
        f'{{status_code="STATUS_CODE_ERROR",service_name="{resolved_service}"}}'
        if scope == "service" and resolved_service
        else '{status_code="STATUS_CODE_ERROR"}'
    )

    if latency_like:
        return (
            'histogram_quantile(0.95, '
            f'sum(rate(traces_span_metrics_duration_milliseconds_bucket{service_filter}[5m])) by (le))'
        )
    if error_like:
        return f'sum(rate(traces_span_metrics_calls_total{error_filter}[5m]))'
    if mode == "throughput":
        return f'sum(rate(traces_span_metrics_calls_total{service_filter}[5m]))'
    return f'sum(rate(traces_span_metrics_calls_total{service_filter}[5m]))'


def _default_time_window(alert_payload: dict[str, Any]) -> tuple[str, str, str]:
    alert = AlertEnvelope.model_validate(alert_payload)
    now = datetime.now(timezone.utc)
    start = alert.timestamps.get("triggered_at") if isinstance(alert.timestamps, dict) else None
    if start is None:
        start = now - timedelta(minutes=15)
    end = alert.timestamps.get("updated_at") if isinstance(alert.timestamps, dict) else None
    if end is None:
        end = now
    start_iso = start.isoformat().replace("+00:00", "Z")
    end_iso = end.isoformat().replace("+00:00", "Z")
    return start_iso, end_iso, "60s"


def bind_artifact_arguments(
    descriptor: McpToolDescriptor,
    arguments: dict[str, Any],
    artifact_state: ArtifactState,
    alert_payload: dict[str, Any],
) -> dict[str, Any]:
    bound = dict(arguments)
    if artifact_state.resolved_service:
        for key in ("service", "service_name", "serviceName"):
            if key in descriptor.arg_keys or key in descriptor.required_args:
                bound[key] = artifact_state.resolved_service

    if artifact_state.resolved_operations:
        if "operation" in descriptor.arg_keys or "operation" in descriptor.required_args:
            bound.setdefault("operation", artifact_state.resolved_operations[0])

    if artifact_state.trace_ids:
        if "trace_id" in descriptor.arg_keys or "trace_id" in descriptor.required_args:
            bound["trace_id"] = artifact_state.trace_ids[0]

    if descriptor.server_id.lower() == "prometheus":
        if descriptor.tool_name == "list_label_values":
            label_candidates = artifact_state.metric_label_keys or ["service_name", "service"]
            preferred = next((item for item in label_candidates if item in {"service_name", "service", "service.name"}), label_candidates[0])
            bound.setdefault("label", preferred)
        elif descriptor.tool_name in {"query_instant", "query_range"}:
            query_mode = "throughput"
            joined = " ".join(artifact_state.alert_terms).lower()
            if any(token in joined for token in ("error", "5xx", "exception", "fail")):
                query_mode = "error"
            elif any(token in joined for token in ("latency", "slow", "slowness", "duration", "timeout")):
                query_mode = "latency"
            if artifact_state.resolved_service:
                bound.setdefault(
                    "query",
                    default_prometheus_query(artifact_state.alert_terms, artifact_state.resolved_service, query_mode),
                )
            if descriptor.tool_name == "query_range":
                start, end, step = _default_time_window(alert_payload)
                bound.setdefault("start", start)
                bound.setdefault("end", end)
                bound.setdefault("step", step)

    return bound
