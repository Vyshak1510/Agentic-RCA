from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from platform_core.agent_runtime import run_planner_agent, run_resolver_agent
from platform_core.evidence_store import EvidenceStore
from platform_core.llm_router import ModelRoute, resolve_model_alias, summarize_with_model_route
from platform_core.mcp_client import invoke_mcp_tool
from platform_core.mcp_planning import build_mcp_only_plan, derive_argument_context
from platform_core.models import (
    AgentPromptProfile,
    AgentRolloutMode,
    AlertEnvelope,
    EvidenceItem,
    Hypothesis,
    InvestigationPlan,
    LlmProviderRoute,
    McpServerConfig,
    McpToolDescriptor,
    RcaReport,
    ServiceIdentity,
    WorkflowStageId,
)
from platform_core.policy import enforce_budget_policy, enforce_citation_policy
from platform_core.policy_service import PolicyService
from platform_core.publisher import Publisher
from platform_core.resolver import resolve_service_identity
from platform_core.store import store


def _execution_policy(run_context: dict[str, Any] | None) -> str:
    if run_context and isinstance(run_context.get("execution_policy"), str):
        return str(run_context.get("execution_policy") or "mcp_only").strip().lower()
    return "mcp_only"


def _deterministic_service_identity(alert_payload: dict[str, Any]) -> dict[str, Any]:
    alert = AlertEnvelope.model_validate(alert_payload)
    entities = list(dict.fromkeys(alert.entity_ids))
    candidates = entities if entities else ["unknown-service"]

    identity = resolve_service_identity(
        alert,
        nr_candidates=candidates,
        azure_candidates=[],
        cmdb_candidates=[],
        rag_candidates=[],
    )
    payload = identity.model_dump(mode="json")
    payload["stage_reasoning_summary"] = "Deterministic resolver selected service identity from alert entities only."
    payload["tool_traces"] = []
    payload["skipped_tools"] = []
    return payload


def _deterministic_mcp_plan(
    investigation_id: str,
    alert_payload: dict[str, Any],
    mcp_tools: list[McpToolDescriptor],
    allowlist: list[str] | None,
) -> dict[str, Any]:
    context = derive_argument_context(alert_payload, {})
    plan, skipped_tools = build_mcp_only_plan(
        investigation_id=investigation_id,
        tools=mcp_tools,
        context=context,
        allowlist=allowlist,
        max_steps=6,
        max_api_calls=10,
        max_stage_wall_clock_seconds=600,
    )
    enforce_budget_policy(plan)
    payload = plan.model_dump(mode="json")
    payload["stage_reasoning_summary"] = "Deterministic MCP planner applied tool schema constraints and budget limits."
    payload["tool_traces"] = []
    payload["skipped_tools"] = skipped_tools
    return payload


def _tenant_and_environment(run_context: dict[str, Any] | None) -> tuple[str, str]:
    if not run_context:
        return "default", "prod"
    tenant = str(run_context.get("tenant") or "default")
    environment = str(run_context.get("environment") or "prod")
    return tenant, environment


def _llm_route(run_context: dict[str, Any] | None, tenant: str, environment: str) -> LlmProviderRoute:
    if run_context and isinstance(run_context.get("llm_route"), dict):
        route_payload = run_context["llm_route"]
        return LlmProviderRoute.model_validate(route_payload)
    return store.get_llm_route(tenant, environment)


def _prompt_profile(
    run_context: dict[str, Any] | None,
    tenant: str,
    environment: str,
    stage_id: WorkflowStageId,
) -> AgentPromptProfile:
    if run_context and isinstance(run_context.get("agent_prompt_profiles"), dict):
        profiles_payload = run_context["agent_prompt_profiles"]
        if isinstance(profiles_payload.get(stage_id.value), dict):
            return AgentPromptProfile.model_validate(profiles_payload[stage_id.value])

    existing = store.get_agent_prompt_profile(tenant, environment, stage_id)
    if existing:
        return existing
    return AgentPromptProfile(
        tenant=tenant,
        environment=environment,
        stage_id=stage_id,
        system_prompt="You are an RCA investigation agent.",
        objective_template="Resolve alert {{incident_key}} with evidence-linked reasoning.",
        max_turns=4,
        max_tool_calls=6,
        tool_allowlist=[],
        updated_at=datetime.now(timezone.utc),
        updated_by="system",
    )


def _rollout_mode(run_context: dict[str, Any] | None, tenant: str, environment: str) -> AgentRolloutMode:
    if run_context and run_context.get("agent_rollout_mode"):
        return AgentRolloutMode(str(run_context["agent_rollout_mode"]))
    return store.get_agent_rollout(tenant, environment).mode


def _mcp_servers(run_context: dict[str, Any] | None, tenant: str, environment: str) -> list[McpServerConfig]:
    if run_context and isinstance(run_context.get("mcp_servers"), list):
        return [McpServerConfig.model_validate(item) for item in run_context["mcp_servers"]]
    return store.list_mcp_servers(tenant=tenant, environment=environment)


def _mcp_tools(run_context: dict[str, Any] | None, tenant: str, environment: str) -> list[McpToolDescriptor]:
    if run_context and isinstance(run_context.get("mcp_tools"), list):
        return [McpToolDescriptor.model_validate(item) for item in run_context["mcp_tools"]]
    return store.list_all_mcp_tools(tenant=tenant, environment=environment)


def _service_identity_diff(deterministic: dict[str, Any], agentic: dict[str, Any]) -> dict[str, Any]:
    deterministic_ambiguous = deterministic.get("ambiguous_candidates") or []
    agent_ambiguous = agentic.get("ambiguous_candidates") or []
    return {
        "canonical_service_id_changed": deterministic.get("canonical_service_id") != agentic.get("canonical_service_id"),
        "deterministic_canonical_service_id": deterministic.get("canonical_service_id"),
        "agent_canonical_service_id": agentic.get("canonical_service_id"),
        "ambiguity_changed": deterministic_ambiguous != agent_ambiguous,
        "deterministic_ambiguous_candidates": deterministic_ambiguous[:3],
        "agent_ambiguous_candidates": agent_ambiguous[:3],
    }


def _plan_diff(deterministic: dict[str, Any], agentic: dict[str, Any]) -> dict[str, Any]:
    def _steps(payload: dict[str, Any]) -> list[tuple[str, str, str | None]]:
        ordered_steps = payload.get("ordered_steps", [])
        if not isinstance(ordered_steps, list):
            return []
        values: list[tuple[str, str, str | None]] = []
        for step in ordered_steps:
            if not isinstance(step, dict):
                continue
            values.append(
                (
                    str(step.get("provider")),
                    str(step.get("capability")),
                    str(step.get("mcp_tool_name")) if step.get("mcp_tool_name") else None,
                )
            )
        return values

    deterministic_steps = _steps(deterministic)
    agent_steps = _steps(agentic)
    return {
        "steps_changed": deterministic_steps != agent_steps,
        "deterministic_steps": deterministic_steps,
        "agent_steps": agent_steps,
        "max_api_calls_changed": deterministic.get("max_api_calls") != agentic.get("max_api_calls"),
        "deterministic_max_api_calls": deterministic.get("max_api_calls"),
        "agent_max_api_calls": agentic.get("max_api_calls"),
    }


def _agent_route_from_llm(route: LlmProviderRoute) -> ModelRoute:
    return ModelRoute(primary=route.primary_model, fallback=route.fallback_model, key_ref=route.key_ref)


def _model_diagnostics(route: LlmProviderRoute) -> tuple[dict[str, str], dict[str, str], str | None]:
    requested = {"primary": route.primary_model, "fallback": route.fallback_model}
    resolved: dict[str, str] = {}
    try:
        resolved["primary"] = resolve_model_alias(route.primary_model)
        resolved["fallback"] = resolve_model_alias(route.fallback_model)
        return requested, resolved, None
    except Exception as exc:
        return requested, resolved, str(exc)


def resolve_service_stage(alert_payload: dict[str, Any], run_context: dict[str, Any] | None = None) -> dict[str, Any]:
    deterministic = _deterministic_service_identity(alert_payload)
    tenant, environment = _tenant_and_environment(run_context)
    rollout = _rollout_mode(run_context, tenant, environment)
    llm_route = _llm_route(run_context, tenant, environment)
    prompt_profile = _prompt_profile(run_context, tenant, environment, WorkflowStageId.RESOLVE_SERVICE_IDENTITY)
    mcp_servers = _mcp_servers(run_context, tenant, environment)
    mcp_tools = _mcp_tools(run_context, tenant, environment)
    requested_model, resolved_model, model_error = _model_diagnostics(llm_route)

    if _execution_policy(run_context) != "mcp_only":
        raise ValueError("Unsupported execution policy. Expected mcp_only.")

    if rollout == AgentRolloutMode.ACTIVE:
        agent_result = run_resolver_agent(
            alert_payload=alert_payload,
            model_route=_agent_route_from_llm(llm_route),
            prompt_profile=prompt_profile,
            mcp_servers=mcp_servers,
            mcp_tools=mcp_tools,
        )
        if agent_result.model_error:
            raise RuntimeError(f"Resolver model failure in active mode: {agent_result.model_error}")
        validated = ServiceIdentity.model_validate(agent_result.payload)
        payload = validated.model_dump(mode="json")
        payload["llm_model_used"] = agent_result.llm_model_used
        payload["llm_summary"] = agent_result.llm_summary
        payload["stage_reasoning_summary"] = agent_result.stage_reasoning_summary
        payload["tool_traces"] = [trace.model_dump(mode="json") for trace in agent_result.tool_traces]
        payload["skipped_tools"] = agent_result.skipped_tools
        payload["requested_model"] = agent_result.requested_model
        payload["resolved_model"] = {**agent_result.resolved_model, "used": agent_result.llm_model_used}
        payload["model_error"] = agent_result.model_error
        payload["agent_rollout_mode"] = rollout.value
        payload["execution_policy"] = "mcp_only"
        return payload

    try:
        agent_result = run_resolver_agent(
            alert_payload=alert_payload,
            model_route=_agent_route_from_llm(llm_route),
            prompt_profile=prompt_profile,
            mcp_servers=mcp_servers,
            mcp_tools=mcp_tools,
        )
        validated = ServiceIdentity.model_validate(agent_result.payload)
        compare_diff = _service_identity_diff(deterministic, validated.model_dump(mode="json"))
        deterministic["agent_compare"] = compare_diff
        deterministic["agent_rollout_mode"] = rollout.value
        deterministic["llm_model_used"] = agent_result.llm_model_used
        deterministic["llm_summary"] = agent_result.llm_summary
        deterministic["stage_reasoning_summary"] = (
            "Compare mode: deterministic output active. "
            f"Agent candidate from {agent_result.llm_model_used} captured for scoring."
        )
        deterministic["tool_traces"] = [trace.model_dump(mode="json") for trace in agent_result.tool_traces]
        deterministic["skipped_tools"] = agent_result.skipped_tools
        deterministic["requested_model"] = agent_result.requested_model
        deterministic["resolved_model"] = (
            {**agent_result.resolved_model, "used": agent_result.llm_model_used}
            if agent_result.resolved_model
            else {}
        )
        deterministic["model_error"] = agent_result.model_error
        if agent_result.model_error:
            deterministic["agent_compare"] = {"agent_error": agent_result.model_error}
            deterministic["stage_reasoning_summary"] = (
                "Compare mode: deterministic output active. "
                f"Agent model call failed and was recorded ({agent_result.model_error})."
            )
    except Exception as exc:
        deterministic["agent_compare"] = {"agent_error": str(exc)}
        deterministic["agent_rollout_mode"] = rollout.value
        deterministic["requested_model"] = requested_model
        deterministic["resolved_model"] = resolved_model
        deterministic["model_error"] = str(exc) or model_error
        deterministic["stage_reasoning_summary"] = (
            "Compare mode: deterministic output active. "
            f"Agent candidate failed and was recorded for scoring ({exc})."
        )
        deterministic["skipped_tools"] = []
    deterministic["execution_policy"] = "mcp_only"
    return deterministic


def build_plan_stage(
    investigation_id: str,
    alert_payload: dict[str, Any],
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tenant, environment = _tenant_and_environment(run_context)
    rollout = _rollout_mode(run_context, tenant, environment)
    llm_route = _llm_route(run_context, tenant, environment)
    prompt_profile = _prompt_profile(run_context, tenant, environment, WorkflowStageId.BUILD_INVESTIGATION_PLAN)
    mcp_servers = _mcp_servers(run_context, tenant, environment)
    mcp_tools = _mcp_tools(run_context, tenant, environment)
    requested_model, resolved_model, model_error = _model_diagnostics(llm_route)

    if _execution_policy(run_context) != "mcp_only":
        raise ValueError("Unsupported execution policy. Expected mcp_only.")

    deterministic = _deterministic_mcp_plan(
        investigation_id,
        alert_payload,
        mcp_tools,
        prompt_profile.tool_allowlist,
    )

    if rollout == AgentRolloutMode.ACTIVE:
        agent_result = run_planner_agent(
            investigation_id=investigation_id,
            alert_payload=alert_payload,
            model_route=_agent_route_from_llm(llm_route),
            prompt_profile=prompt_profile,
            mcp_servers=mcp_servers,
            mcp_tools=mcp_tools,
        )
        if agent_result.model_error:
            raise RuntimeError(f"Planner model failure in active mode: {agent_result.model_error}")
        validated = InvestigationPlan.model_validate(agent_result.payload)
        payload = validated.model_dump(mode="json")
        payload["llm_model_used"] = agent_result.llm_model_used
        payload["llm_summary"] = agent_result.llm_summary
        payload["stage_reasoning_summary"] = agent_result.stage_reasoning_summary
        payload["tool_traces"] = [trace.model_dump(mode="json") for trace in agent_result.tool_traces]
        payload["skipped_tools"] = agent_result.skipped_tools
        payload["requested_model"] = agent_result.requested_model
        payload["resolved_model"] = {**agent_result.resolved_model, "used": agent_result.llm_model_used}
        payload["model_error"] = agent_result.model_error
        payload["agent_rollout_mode"] = rollout.value
        payload["execution_policy"] = "mcp_only"
        return payload

    try:
        agent_result = run_planner_agent(
            investigation_id=investigation_id,
            alert_payload=alert_payload,
            model_route=_agent_route_from_llm(llm_route),
            prompt_profile=prompt_profile,
            mcp_servers=mcp_servers,
            mcp_tools=mcp_tools,
        )
        validated = InvestigationPlan.model_validate(agent_result.payload)
        compare_diff = _plan_diff(deterministic, validated.model_dump(mode="json"))
        deterministic["agent_compare"] = compare_diff
        deterministic["agent_rollout_mode"] = rollout.value
        deterministic["llm_model_used"] = agent_result.llm_model_used
        deterministic["llm_summary"] = agent_result.llm_summary
        deterministic["stage_reasoning_summary"] = (
            "Compare mode: deterministic plan active. "
            f"Agent candidate from {agent_result.llm_model_used} captured for scoring."
        )
        deterministic["tool_traces"] = [trace.model_dump(mode="json") for trace in agent_result.tool_traces]
        deterministic["skipped_tools"] = agent_result.skipped_tools
        deterministic["requested_model"] = agent_result.requested_model
        deterministic["resolved_model"] = (
            {**agent_result.resolved_model, "used": agent_result.llm_model_used}
            if agent_result.resolved_model
            else {}
        )
        deterministic["model_error"] = agent_result.model_error
        if agent_result.model_error:
            deterministic["agent_compare"] = {"agent_error": agent_result.model_error}
            deterministic["stage_reasoning_summary"] = (
                "Compare mode: deterministic plan active. "
                f"Agent model call failed and was recorded ({agent_result.model_error})."
            )
    except Exception as exc:
        deterministic["agent_compare"] = {"agent_error": str(exc)}
        deterministic["agent_rollout_mode"] = rollout.value
        deterministic["requested_model"] = requested_model
        deterministic["resolved_model"] = resolved_model
        deterministic["model_error"] = str(exc) or model_error
        deterministic["stage_reasoning_summary"] = (
            "Compare mode: deterministic plan active. "
            f"Agent candidate failed and was recorded for scoring ({exc})."
        )
    deterministic["execution_policy"] = "mcp_only"
    return deterministic


def collect_evidence_stage(
    investigation_id: str,
    alert_payload: dict[str, Any],
    plan_payload: dict[str, Any],
    run_context: dict[str, Any] | None = None,
    early_stop_min_citations: int = 3,
) -> dict[str, Any]:
    alert = AlertEnvelope.model_validate(alert_payload)
    plan = InvestigationPlan.model_validate(plan_payload)

    if _execution_policy(run_context) != "mcp_only":
        raise ValueError("Unsupported execution policy. Expected mcp_only.")

    tenant, environment = _tenant_and_environment(run_context)
    if run_context and isinstance(run_context.get("mcp_servers"), list):
        servers = {
            server.server_id: server
            for server in [McpServerConfig.model_validate(item) for item in run_context["mcp_servers"]]
            if server.enabled
        }
    else:
        servers = {
            server.server_id: server
            for server in store.list_mcp_servers(tenant=tenant, environment=environment)
            if server.enabled
        }

    policy_service = PolicyService()
    evidence_store = EvidenceStore()
    provider_signal_counts: defaultdict[str, int] = defaultdict(int)

    timeline: list[str] = []
    execution_trace: list[dict[str, Any]] = []
    skipped_tools: list[dict[str, Any]] = []
    executed_steps = 0
    stopped_early = False

    for step in plan.ordered_steps:
        executed_steps += 1
        if step.execution_source != "mcp" or not step.mcp_server_id or not step.mcp_tool_name:
            raise ValueError(
                f"Non-MCP plan step detected in MCP-only mode (step_index={executed_steps}, provider={step.provider})."
            )

        server = servers.get(step.mcp_server_id)
        if not server:
            raise ValueError(f"MCP server unavailable for step {executed_steps}: {step.mcp_server_id}")

        arguments = dict(step.mcp_arguments or {})
        trace_entry: dict[str, Any] = {
            "step_index": executed_steps,
            "provider": step.provider,
            "capability": step.capability,
            "execution_source": step.execution_source,
            "mcp_server_id": step.mcp_server_id,
            "mcp_tool_name": step.mcp_tool_name,
            "arguments": arguments,
            "signal_count": 0,
            "early_stop_after_step": False,
            "status": "completed",
        }

        try:
            result = invoke_mcp_tool(server, step.mcp_tool_name, arguments)
            signals: list[dict[str, Any]] = []
            if isinstance(result, dict) and isinstance(result.get("content"), list):
                for content_item in result["content"]:
                    if isinstance(content_item, dict):
                        signals.append(content_item)
            if not signals:
                signals = [result if isinstance(result, dict) else {"result": result}]

            for signal in signals:
                evidence = evidence_store.add(
                    investigation_id=investigation_id,
                    provider=step.mcp_server_id,
                    evidence_type=step.mcp_tool_name,
                    payload=signal,
                )
                provider_signal_counts[step.mcp_server_id] += 1
                policy_service.validate_redaction_state(evidence.redaction_state)

            trace_entry["signal_count"] = len(signals)
            timeline.append(f"Collected {len(signals)} signal(s) from mcp.{step.mcp_server_id}.{step.mcp_tool_name}")
        except Exception as exc:
            trace_entry["status"] = "failed"
            trace_entry["error"] = str(exc)
            skipped_tools.append(
                {
                    "tool_name": f"mcp.{step.mcp_server_id}.{step.mcp_tool_name}",
                    "reason": "execution_error",
                    "error": str(exc),
                }
            )
            timeline.append(f"MCP tool failed mcp.{step.mcp_server_id}.{step.mcp_tool_name}: {exc}")

        trace_entry["evidence_count_after_step"] = len(evidence_store.list(investigation_id))

        evidence_items = evidence_store.list(investigation_id)
        if len(evidence_items) >= early_stop_min_citations:
            top_provider_hits = max(provider_signal_counts.values(), default=0)
            tied_top = sum(1 for count in provider_signal_counts.values() if count == top_provider_hits) > 1
            if top_provider_hits >= 2 and not tied_top:
                stopped_early = True
                timeline.append("Early stop: confidence threshold reached without conflicting top signals.")
                trace_entry["early_stop_after_step"] = True
                execution_trace.append(trace_entry)
                break
        execution_trace.append(trace_entry)

    evidence_items = evidence_store.list(investigation_id)
    if not evidence_items:
        fallback_evidence = evidence_store.add(
            investigation_id=investigation_id,
            provider="mcp",
            evidence_type="alert-context",
            payload={"incident_key": alert.incident_key, "raw_payload_ref": alert.raw_payload_ref},
        )
        policy_service.validate_redaction_state(fallback_evidence.redaction_state)
        evidence_items = [fallback_evidence]
        timeline.append("No MCP signals found; used alert-context fallback evidence.")

    return {
        "executed_steps": executed_steps,
        "stopped_early": stopped_early,
        "timeline": timeline,
        "execution_trace": execution_trace,
        "skipped_tools": skipped_tools,
        "evidence": [item.model_dump(mode="json") for item in evidence_items],
    }


def synthesize_report_stage(
    alert_payload: dict[str, Any],
    service_identity_payload: dict[str, Any],
    evidence_payload: list[dict[str, Any]],
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ = AlertEnvelope.model_validate(alert_payload)
    service_identity = ServiceIdentity.model_validate(service_identity_payload)
    evidence_items = [EvidenceItem.model_validate(item) for item in evidence_payload]

    citations_by_provider: defaultdict[str, list[str]] = defaultdict(list)
    for item in evidence_items:
        citations_by_provider[item.provider].append(item.citation_id)

    provider_rank = sorted(citations_by_provider.items(), key=lambda kv: len(kv[1]), reverse=True)
    ranking_trace = [
        {
            "provider": provider,
            "citation_count": len(citations),
            "sample_citations": citations[:3],
        }
        for provider, citations in provider_rank
    ]
    templates = {
        "grafana": "Grafana alerting/annotation signals indicate elevated failure conditions in the target scope.",
        "jaeger": "Trace anomalies indicate latency/error concentration in a request path dependency.",
        "mcp": "MCP-collected context indicates a service-level degradation pattern requiring manual mitigation.",
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

    tenant, environment = _tenant_and_environment(run_context)
    llm_route = _llm_route(run_context, tenant, environment)
    route = _agent_route_from_llm(llm_route)
    prompt = (
        f"Synthesize RCA for service {service_identity.canonical_service_id} "
        f"using {len(evidence_items)} evidence item(s)."
    )
    model_used, llm_summary = summarize_with_model_route(
        route,
        prompt,
        system_prompt=(
            "You are an RCA synthesis assistant. Summarize likely cause and evidence strength in concise text."
        ),
        max_tokens=320,
    )

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
        "requested_model": {"primary": llm_route.primary_model, "fallback": llm_route.fallback_model},
        "resolved_model": {
            "primary": resolve_model_alias(llm_route.primary_model),
            "fallback": resolve_model_alias(llm_route.fallback_model),
            "used": model_used,
        },
        "model_error": None,
        "synthesis_trace": {
            "service": service_identity.canonical_service_id,
            "evidence_count": len(evidence_items),
            "provider_ranking": ranking_trace,
        },
    }


def publish_stage(alert_payload: dict[str, Any], report_payload: dict[str, Any], enabled: bool = True) -> dict[str, Any]:
    alert = AlertEnvelope.model_validate(alert_payload)
    report = RcaReport.model_validate(report_payload)

    if not enabled:
        return {
            "published": False,
            "slack_message_id": None,
            "jira_issue_key": None,
            "publish_trace": {"slack_enabled": False, "jira_enabled": False, "mode": "skipped"},
        }

    publish_result = Publisher().publish(report=report, incident_key=alert.incident_key)
    return {
        "published": True,
        "slack_message_id": publish_result.slack_message_id,
        "jira_issue_key": publish_result.jira_issue_key,
        "publish_trace": {
            "slack_enabled": True,
            "jira_enabled": True,
            "mode": "stubbed",
            "incident_key": alert.incident_key,
        },
    }


def emit_eval_event_stage(
    investigation_id: str,
    report_payload: dict[str, Any],
    evidence_payload: list[dict[str, Any]],
    latency_seconds: float,
) -> dict[str, Any]:
    report = RcaReport.model_validate(report_payload)
    evidence = [EvidenceItem.model_validate(item) for item in evidence_payload]
    citation_count = sum(len(h.supporting_citations) for h in report.top_hypotheses)
    return {
        "investigation_id": investigation_id,
        "top_hypothesis_count": len(report.top_hypotheses),
        "citation_count": citation_count,
        "evidence_count": len(evidence),
        "latency_seconds": round(latency_seconds, 2),
        "requires_human_review": True,
        "required_human_review_percent": 100,
        "rollout_mode": "shadow",
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "eval_trace": {
            "gate_metrics": {
                "top_hypothesis_count": len(report.top_hypotheses),
                "citation_count": citation_count,
                "evidence_count": len(evidence),
            },
            "latency_seconds": round(latency_seconds, 2),
        },
    }
