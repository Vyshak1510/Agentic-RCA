from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from platform_core.agent_runtime import AgentExecutionResult
from platform_core.models import AgentToolTrace
from services.orchestrator.app.pipeline import (
    build_plan_stage,
    collect_evidence_stage,
    emit_eval_event_stage,
    publish_stage,
    resolve_service_stage,
    synthesize_report_stage,
)


def _sample_alert() -> dict:
    return {
        "source": "newrelic",
        "severity": "critical",
        "incident_key": f"nr-{uuid4()}",
        "entity_ids": ["service-checkout"],
        "timestamps": {"triggered_at": datetime.now(timezone.utc).isoformat()},
        "raw_payload_ref": "newrelic://sample",
        "raw_payload": {"owner": "payments-team", "env": "prod"},
    }


def _stub_summarize_with_model_route(*_: object, **__: object) -> tuple[str, str]:
    return "openai/mock", "mock summary"


def _active_run_context(now: str | None = None) -> dict:
    timestamp = now or datetime.now(timezone.utc).isoformat()
    return {
        "tenant": "default",
        "environment": "prod",
        "agent_rollout_mode": "active",
        "llm_route": {
            "tenant": "default",
            "environment": "prod",
            "primary_model": "codex",
            "fallback_model": "claude",
            "key_ref": "llm-provider-secret",
        },
        "mcp_servers": [
            {
                "server_id": "jaeger",
                "tenant": "default",
                "environment": "prod",
                "transport": "http_sse",
                "base_url": "http://jaeger-mcp:8000/mcp",
                "secret_ref_name": None,
                "secret_ref_key": None,
                "timeout_seconds": 8,
                "enabled": True,
                "updated_at": timestamp,
                "updated_by": "test",
            },
            {
                "server_id": "prometheus",
                "tenant": "default",
                "environment": "prod",
                "transport": "http_sse",
                "base_url": "http://prometheus-mcp:8000/mcp",
                "secret_ref_name": None,
                "secret_ref_key": None,
                "timeout_seconds": 8,
                "enabled": True,
                "updated_at": timestamp,
                "updated_by": "test",
            },
        ],
    }


def test_pipeline_generates_rca_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCA_MODEL_ALIAS_CODEX", "openai/mock-primary")
    monkeypatch.setenv("RCA_MODEL_ALIAS_CLAUDE", "anthropic/mock-fallback")
    monkeypatch.setattr(
        "platform_core.agent_runtime.summarize_with_model_route",
        _stub_summarize_with_model_route,
    )
    monkeypatch.setattr(
        "services.orchestrator.app.pipeline.summarize_with_model_route",
        _stub_summarize_with_model_route,
    )
    investigation_id = f"inv-{uuid4()}"
    alert = _sample_alert()

    service_identity = resolve_service_stage(alert)
    assert service_identity["canonical_service_id"]

    plan = build_plan_stage(investigation_id, alert)
    assert isinstance(plan["ordered_steps"], list)

    evidence_result = collect_evidence_stage(investigation_id, alert, plan)
    assert evidence_result["evidence"]

    synthesis = synthesize_report_stage(alert, service_identity, evidence_result["evidence"])
    report = synthesis["report"]
    assert len(report["top_hypotheses"]) <= 3
    for hypothesis in report["top_hypotheses"]:
        assert hypothesis["supporting_citations"]

    publish_result = publish_stage(alert, report, enabled=False)
    assert publish_result["published"] is False

    eval_event = emit_eval_event_stage(investigation_id, report, evidence_result["evidence"], latency_seconds=42.0)
    assert eval_event["requires_human_review"] is True
    assert eval_event["rollout_mode"] == "compare"


def test_resolver_compare_mode_adds_agent_diff_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCA_MODEL_ALIAS_CODEX", "openai/mock-primary")
    monkeypatch.setenv("RCA_MODEL_ALIAS_CLAUDE", "anthropic/mock-fallback")
    monkeypatch.setattr(
        "platform_core.agent_runtime.summarize_with_model_route",
        _stub_summarize_with_model_route,
    )
    alert = _sample_alert()
    run_context = {
        "tenant": "default",
        "environment": "prod",
        "agent_rollout_mode": "compare",
        "llm_route": {
            "tenant": "default",
            "environment": "prod",
            "primary_model": "codex",
            "fallback_model": "claude",
            "key_ref": "llm-provider-secret",
        },
        "agent_prompt_profiles": {},
        "mcp_servers": [],
        "mcp_tools": [],
    }

    result = resolve_service_stage(alert, run_context)
    assert result["agent_rollout_mode"] == "compare"
    assert "agent_compare" in result
    assert result["stage_reasoning_summary"]
    assert isinstance(result.get("tool_traces"), list)


def test_planner_compare_mode_uses_light_probe_tools_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCA_MODEL_ALIAS_CODEX", "openai/mock-primary")
    monkeypatch.setenv("RCA_MODEL_ALIAS_CLAUDE", "anthropic/mock-fallback")
    monkeypatch.setattr(
        "platform_core.agent_runtime.summarize_with_model_route",
        _stub_summarize_with_model_route,
    )
    investigation_id = f"inv-{uuid4()}"
    alert = _sample_alert()
    now = datetime.now(timezone.utc).isoformat()
    run_context = {
        "tenant": "default",
        "environment": "prod",
        "agent_rollout_mode": "compare",
        "llm_route": {
            "tenant": "default",
            "environment": "prod",
            "primary_model": "codex",
            "fallback_model": "claude",
            "key_ref": "llm-provider-secret",
        },
        "agent_prompt_profiles": {},
        "mcp_servers": [
            {
                "server_id": "jaeger",
                "tenant": "default",
                "environment": "prod",
                "transport": "http_sse",
                "base_url": "http://jaeger-mcp:8000/mcp",
                "secret_ref_name": None,
                "secret_ref_key": None,
                "timeout_seconds": 8,
                "enabled": True,
                "updated_at": now,
                "updated_by": "test",
            }
        ],
        "mcp_tools": [
            {
                "server_id": "jaeger",
                "tool_name": "list_services",
                "description": "list services",
                "capabilities": ["tracing"],
                "read_only": True,
                "light_probe": True,
            }
        ],
    }

    result = build_plan_stage(investigation_id, alert, run_context)
    assert result["agent_rollout_mode"] == "compare"
    assert "agent_compare" in result
    traces = result.get("tool_traces", [])
    assert traces
    for trace in traces:
        assert ".collect." not in trace["tool_name"]


def test_planner_outputs_mcp_only_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCA_MODEL_ALIAS_CODEX", "openai/mock-primary")
    monkeypatch.setenv("RCA_MODEL_ALIAS_CLAUDE", "anthropic/mock-fallback")
    monkeypatch.setattr(
        "platform_core.agent_runtime.summarize_with_model_route",
        _stub_summarize_with_model_route,
    )

    investigation_id = f"inv-{uuid4()}"
    alert = _sample_alert()
    now = datetime.now(timezone.utc).isoformat()
    run_context = {
        "tenant": "default",
        "environment": "prod",
        "agent_rollout_mode": "active",
        "llm_route": {
            "tenant": "default",
            "environment": "prod",
            "primary_model": "codex",
            "fallback_model": "claude",
            "key_ref": "llm-provider-secret",
        },
        "mcp_servers": [
            {
                "server_id": "jaeger",
                "tenant": "default",
                "environment": "prod",
                "transport": "http_sse",
                "base_url": "http://jaeger-mcp:8000/mcp",
                "secret_ref_name": None,
                "secret_ref_key": None,
                "timeout_seconds": 8,
                "enabled": True,
                "updated_at": now,
                "updated_by": "test",
            }
        ],
        "mcp_tools": [
            {
                "server_id": "jaeger",
                "tool_name": "search_traces",
                "description": "search traces",
                "capabilities": ["tracing"],
                "read_only": True,
                "light_probe": False,
                "arg_keys": ["service"],
                "required_args": ["service"],
            }
        ],
    }

    result = build_plan_stage(investigation_id, alert, run_context)
    assert result["ordered_steps"]
    for step in result["ordered_steps"]:
        assert step["provider"] == "mcp"
        assert step["execution_source"] == "mcp"
        assert step["mcp_server_id"] == "jaeger"


def test_resolver_and_planner_emit_artifact_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCA_MODEL_ALIAS_CODEX", "openai/mock-primary")
    monkeypatch.setenv("RCA_MODEL_ALIAS_CLAUDE", "anthropic/mock-fallback")
    monkeypatch.setattr(
        "platform_core.agent_runtime.summarize_with_model_route",
        _stub_summarize_with_model_route,
    )
    alert = {
        **_sample_alert(),
        "entity_ids": ["recommendationservice"],
        "raw_payload": {"title": "high error rate on recommendationservice", "env": "prod"},
    }
    now = datetime.now(timezone.utc).isoformat()
    run_context = {
        "tenant": "default",
        "environment": "prod",
        "agent_rollout_mode": "active",
        "llm_route": {
            "tenant": "default",
            "environment": "prod",
            "primary_model": "codex",
            "fallback_model": "claude",
            "key_ref": "llm-provider-secret",
        },
        "mcp_servers": [
            {
                "server_id": "jaeger",
                "tenant": "default",
                "environment": "prod",
                "transport": "http_sse",
                "base_url": "http://jaeger-mcp:8000/mcp",
                "secret_ref_name": None,
                "secret_ref_key": None,
                "timeout_seconds": 8,
                "enabled": True,
                "updated_at": now,
                "updated_by": "test",
            }
        ],
        "mcp_tools": [
            {
                "server_id": "jaeger",
                "tool_name": "get_services",
                "description": "discover services",
                "capabilities": ["tracing"],
                "read_only": True,
                "light_probe": True,
                "arg_keys": [],
                "required_args": [],
            },
            {
                "server_id": "jaeger",
                "tool_name": "search_traces",
                "description": "search traces",
                "capabilities": ["tracing"],
                "read_only": True,
                "light_probe": False,
                "arg_keys": ["service"],
                "required_args": ["service"],
            },
        ],
    }

    resolved = resolve_service_stage(alert, run_context)
    assert "artifact_state" in resolved
    assert "resolved_aliases" in resolved

    run_context["service_identity"] = resolved
    run_context["resolver_artifact_state"] = resolved["artifact_state"]
    plan = build_plan_stage(f"inv-{uuid4()}", alert, run_context)
    assert "artifact_state" in plan
    assert "blocked_tools" in plan
    for step in plan["ordered_steps"]:
        if step.get("mcp_tool_name") == "search_traces":
            assert isinstance(step["mcp_arguments"].get("service"), str)


def test_resolver_prefers_entity_ids_over_summary_noise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCA_MODEL_ALIAS_CODEX", "openai/mock-primary")
    monkeypatch.setenv("RCA_MODEL_ALIAS_CLAUDE", "anthropic/mock-fallback")

    def _resolver_stub(*_: object, **__: object) -> AgentExecutionResult:
        started = datetime.now(timezone.utc)
        return AgentExecutionResult(
            payload={
                "canonical_service_id": "recommendation",
                "owner": "payments-team",
                "env": "prod",
                "dependency_graph_refs": [],
                "mapped_provider_ids": {"jaeger": "recommendation"},
                "confidence": 0.84,
                "ambiguous_candidates": ["ad"],
            },
            llm_model_used="openai/mock-primary",
            llm_summary="resolved recommendation from entity ids",
            stage_reasoning_summary="resolver selected anchored service candidate",
            tool_traces=[
                AgentToolTrace(
                    tool_name="mcp.jaeger.get_services",
                    source="mcp",
                    read_only=True,
                    started_at=started,
                    ended_at=started,
                    duration_ms=5,
                    success=True,
                    args_summary={},
                    result_summary={"service_count": 3},
                    error=None,
                    citations=[],
                )
            ],
            skipped_tools=[],
            requested_model={"primary": "codex", "fallback": "claude"},
            resolved_model={"primary": "openai/mock-primary", "fallback": "anthropic/mock-fallback"},
            artifact_state={
                "alert_terms": ["recommendationservice", "ad service degraded"],
                "entity_terms": ["recommendationservice"],
                "explicit_service_terms": ["recommendationservice"],
                "title_terms": ["recommendationservice"],
                "summary_terms": ["ad", "service", "degraded"],
                "service_candidates": ["recommendation", "ad"],
                "resolved_service": "recommendation",
                "service_aliases": [
                    {
                        "alert_term": "recommendationservice",
                        "resolved_value": "recommendation",
                        "source": "entity_ids",
                        "confidence": 0.84,
                        "candidates": ["recommendation", "ad"],
                    }
                ],
                "alias_decision_trace": {
                    "strategy": "anchor_priority",
                    "selected_candidate": "recommendation",
                    "matched_term": "recommendationservice",
                    "matched_term_source": "entity_ids",
                    "confidence": 0.84,
                    "ambiguous_candidates": ["ad"],
                    "top_candidates": [
                        {
                            "term": "recommendationservice",
                            "term_source": "entity_ids",
                            "candidate": "recommendation",
                            "score": 0.84,
                        },
                        {
                            "term": "ad",
                            "term_source": "summary",
                            "candidate": "ad",
                            "score": 0.99,
                        },
                    ],
                    "unresolved_reason": None,
                },
                "operation_candidates": [],
                "resolved_operations": [],
                "trace_ids": [],
                "datasource_ids": [],
                "dashboard_uids": [],
                "annotation_tags": [],
                "metric_label_keys": [],
                "metric_service_candidates": [],
                "trace_summaries": [],
                "dependency_edges": [],
                "root_cause_signals": [],
            },
            resolved_aliases=[
                {
                    "alert_term": "recommendationservice",
                    "resolved_value": "recommendation",
                    "source": "entity_ids",
                    "confidence": 0.84,
                    "candidates": ["recommendation", "ad"],
                }
            ],
            blocked_tools=[],
            invocable_tools=["mcp.jaeger.get_operations"],
        )

    monkeypatch.setattr("services.orchestrator.app.pipeline.run_resolver_agent", _resolver_stub)
    run_context = _active_run_context()
    run_context["mcp_tools"] = [
        {
            "server_id": "jaeger",
            "tool_name": "get_services",
            "description": "discover services",
            "capabilities": ["tracing"],
            "read_only": True,
            "light_probe": True,
            "arg_keys": [],
            "required_args": [],
        }
    ]
    alert = {
        **_sample_alert(),
        "entity_ids": ["recommendationservice"],
        "raw_payload": {
            "service": "recommendationservice",
            "title": "Recommendation service degraded",
            "summary": "ad service degraded in production while recommendationservice alerts are firing",
            "env": "prod",
        },
    }

    result = resolve_service_stage(alert, run_context)
    assert result["canonical_service_id"] == "recommendation"
    assert result["alias_decision_trace"]["matched_term_source"] == "entity_ids"
    assert result["stage_eval_records"][0]["status"] == "warn"
    assert "summary_only_alias_resolution" not in result["stage_eval_records"][0]["findings"]


def test_resolver_unresolved_alias_does_not_mark_canonical_service_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCA_MODEL_ALIAS_CODEX", "openai/mock-primary")
    monkeypatch.setenv("RCA_MODEL_ALIAS_CLAUDE", "anthropic/mock-fallback")

    def _resolver_stub(*_: object, **__: object) -> AgentExecutionResult:
        started = datetime.now(timezone.utc)
        return AgentExecutionResult(
            payload={
                "canonical_service_id": "unknown",
                "owner": "payments-team",
                "env": "prod",
                "dependency_graph_refs": [],
                "mapped_provider_ids": {},
                "confidence": 0.0,
                "ambiguous_candidates": ["checkout", "cart"],
            },
            llm_model_used="openai/mock-primary",
            llm_summary="unable to resolve service",
            stage_reasoning_summary="resolver could not resolve a canonical service",
            tool_traces=[
                AgentToolTrace(
                    tool_name="mcp.jaeger.get_services",
                    source="mcp",
                    read_only=True,
                    started_at=started,
                    ended_at=started,
                    duration_ms=5,
                    success=True,
                    args_summary={},
                    result_summary={"service_count": 0},
                    error=None,
                    citations=[],
                )
            ],
            skipped_tools=[],
            requested_model={"primary": "codex", "fallback": "claude"},
            resolved_model={"primary": "openai/mock-primary", "fallback": "anthropic/mock-fallback"},
            artifact_state={
                "alert_terms": ["service-checkout"],
                "entity_terms": ["service-checkout"],
                "explicit_service_terms": ["service-checkout"],
                "title_terms": [],
                "summary_terms": [],
                "service_candidates": [],
                "resolved_service": None,
                "service_aliases": [],
                "alias_decision_trace": {
                    "strategy": "anchor_priority",
                    "selected_candidate": None,
                    "matched_term": None,
                    "matched_term_source": None,
                    "confidence": 0.0,
                    "ambiguous_candidates": [],
                    "top_candidates": [],
                    "unresolved_reason": "no_service_candidates",
                },
                "operation_candidates": [],
                "resolved_operations": [],
                "trace_ids": [],
                "datasource_ids": [],
                "dashboard_uids": [],
                "annotation_tags": [],
                "metric_label_keys": [],
                "metric_service_candidates": [],
                "trace_summaries": [],
                "dependency_edges": [],
                "root_cause_signals": [],
            },
            resolved_aliases=[],
            blocked_tools=[],
            invocable_tools=[],
        )

    monkeypatch.setattr("services.orchestrator.app.pipeline.run_resolver_agent", _resolver_stub)
    run_context = _active_run_context()
    run_context["mcp_tools"] = [
        {
            "server_id": "jaeger",
            "tool_name": "get_services",
            "description": "discover services",
            "capabilities": ["tracing"],
            "read_only": True,
            "light_probe": True,
            "arg_keys": [],
            "required_args": [],
        }
    ]

    result = resolve_service_stage(_sample_alert(), run_context)
    assert result["canonical_service_id"] == "unknown"
    assert "canonical_service_selected" not in result["mission_checklist"]["completed"]
    assert result["stage_eval_records"][0]["status"] == "fail"
    assert "resolved_service_missing" in result["stage_eval_records"][0]["findings"]


def test_planner_rejects_bad_scope_and_requests_resolver_rerun(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCA_MODEL_ALIAS_CODEX", "openai/mock-primary")
    monkeypatch.setenv("RCA_MODEL_ALIAS_CLAUDE", "anthropic/mock-fallback")

    def _planner_stub(*_: object, **__: object) -> AgentExecutionResult:
        started = datetime.now(timezone.utc)
        return AgentExecutionResult(
            payload={
                "investigation_id": "inv-bad-scope",
                "ordered_steps": [
                    {
                        "provider": "mcp",
                        "rationale": "inspect wrong service",
                        "timeout_seconds": 30,
                        "budget_weight": 1,
                        "capability": "tracing",
                        "execution_source": "mcp",
                        "mcp_server_id": "jaeger",
                        "mcp_tool_name": "search_traces",
                        "mcp_arguments": {"service": "ad"},
                        "required_artifacts": ["resolved_service"],
                        "produced_artifacts": ["trace_ids"],
                    }
                ],
                "max_api_calls": 10,
                "max_stage_wall_clock_seconds": 600,
            },
            llm_model_used="openai/mock-primary",
            llm_summary="planner emitted weak scope",
            stage_reasoning_summary="planner chose ad despite recommendation alert",
            tool_traces=[
                AgentToolTrace(
                    tool_name="mcp.jaeger.get_services",
                    source="mcp",
                    read_only=True,
                    started_at=started,
                    ended_at=started,
                    duration_ms=5,
                    success=True,
                    args_summary={},
                    result_summary={"service_count": 3},
                    error=None,
                    citations=[],
                )
            ],
            skipped_tools=[],
            requested_model={"primary": "codex", "fallback": "claude"},
            resolved_model={"primary": "openai/mock-primary", "fallback": "anthropic/mock-fallback"},
            artifact_state={
                "alert_terms": ["recommendationservice"],
                "entity_terms": ["recommendationservice"],
                "explicit_service_terms": ["recommendationservice"],
                "title_terms": [],
                "summary_terms": ["ad"],
                "service_candidates": ["recommendation", "ad"],
                "resolved_service": "ad",
                "service_aliases": [],
                "alias_decision_trace": {
                    "strategy": "anchor_priority",
                    "selected_candidate": "ad",
                    "matched_term": "ad",
                    "matched_term_source": "summary",
                    "confidence": 0.41,
                    "ambiguous_candidates": ["recommendation"],
                    "top_candidates": [],
                    "unresolved_reason": None,
                },
                "operation_candidates": [],
                "resolved_operations": [],
                "trace_ids": [],
                "datasource_ids": [],
                "dashboard_uids": [],
                "annotation_tags": [],
                "metric_label_keys": [],
                "metric_service_candidates": [],
                "trace_summaries": [],
                "dependency_edges": [],
                "root_cause_signals": [],
            },
            resolved_aliases=[],
            blocked_tools=[],
            invocable_tools=["mcp.jaeger.search_traces"],
        )

    monkeypatch.setattr("services.orchestrator.app.pipeline.run_planner_agent", _planner_stub)
    run_context = _active_run_context()
    run_context["mcp_tools"] = [
        {
            "server_id": "jaeger",
            "tool_name": "get_services",
            "description": "discover services",
            "capabilities": ["tracing"],
            "read_only": True,
            "light_probe": True,
            "arg_keys": [],
            "required_args": [],
        },
        {
            "server_id": "jaeger",
            "tool_name": "search_traces",
            "description": "search traces",
            "capabilities": ["tracing"],
            "read_only": True,
            "light_probe": False,
            "arg_keys": ["service"],
            "required_args": ["service"],
        },
    ]
    run_context["service_identity"] = {
        "canonical_service_id": "recommendation",
        "owner": None,
        "env": "prod",
        "dependency_graph_refs": [],
        "mapped_provider_ids": {"jaeger": "recommendation"},
        "confidence": 0.85,
        "ambiguous_candidates": [],
    }
    run_context["resolver_artifact_state"] = {
        "alert_terms": ["recommendationservice"],
        "entity_terms": ["recommendationservice"],
        "explicit_service_terms": ["recommendationservice"],
        "title_terms": [],
        "summary_terms": [],
        "service_candidates": ["recommendation"],
        "resolved_service": "recommendation",
        "service_aliases": [],
        "operation_candidates": [],
        "resolved_operations": [],
        "trace_ids": [],
        "datasource_ids": [],
        "dashboard_uids": [],
        "annotation_tags": [],
        "metric_label_keys": [],
        "metric_service_candidates": [],
        "trace_summaries": [],
        "dependency_edges": [],
        "root_cause_signals": [],
    }

    result = build_plan_stage("inv-bad-scope", {"source": "manual", "severity": "critical", "incident_key": "inc-1", "entity_ids": ["recommendationservice"], "timestamps": {}, "raw_payload": {"service": "recommendationservice"}}, run_context)
    assert result["plan_valid"] is False
    assert result["plan_validation_errors"]
    assert any(
        error in result["plan_validation_errors"]
        for error in ("resolved_service_low_confidence", "service_scoped_step_mismatch")
    )
    assert result["rerun_directives"][0]["target_stage"] == "resolve_service_identity"


def test_infra_resource_anomaly_requires_prometheus_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.orchestrator.app.pipeline.summarize_with_model_route",
        _stub_summarize_with_model_route,
    )
    investigation_id = f"inv-{uuid4()}"
    now = datetime.now(timezone.utc).isoformat()
    alert = {
        **_sample_alert(),
        "entity_ids": ["recommendationservice"],
        "raw_payload": {
            "service": "recommendationservice",
            "title": "Recommendation service latency and resource usage are abnormal",
            "symptoms": ["resource usage abnormal", "latency elevated"],
        },
    }

    def _stub_execute(servers: object, descriptor: object, arguments: object) -> tuple[dict, AgentToolTrace]:
        _ = servers
        _ = arguments
        started = datetime.now(timezone.utc)
        trace = AgentToolTrace(
            tool_name=f"mcp.{descriptor.server_id}.{descriptor.tool_name}",
            source="mcp",
            read_only=True,
            started_at=started,
            ended_at=started,
            duration_ms=5,
            success=True,
            args_summary={},
            result_summary={"annotation_count": 1},
            error=None,
            citations=[],
        )
        return {"content": [{"type": "text", "text": '{"annotation":"deploy"}'}]}, trace

    monkeypatch.setattr("services.orchestrator.app.pipeline._execute_mcp_tool", _stub_execute)

    run_context = {
        "tenant": "default",
        "environment": "prod",
        "execution_policy": "mcp_only",
        "llm_route": {
            "tenant": "default",
            "environment": "prod",
            "primary_model": "openai/mock",
            "fallback_model": "openai/mock",
            "key_ref": "llm-provider-secret",
        },
        "mcp_servers": [
            {
                "server_id": "grafana",
                "tenant": "default",
                "environment": "prod",
                "transport": "http_sse",
                "base_url": "http://grafana-mcp:8000/mcp",
                "timeout_seconds": 8,
                "enabled": True,
                "updated_at": now,
                "updated_by": "test",
            }
        ],
        "mcp_tools": [
            {
                "server_id": "grafana",
                "tool_name": "get_annotations",
                "description": "get annotations",
                "capabilities": ["metrics"],
                "read_only": True,
                "light_probe": True,
                "arg_keys": [],
                "required_args": [],
            }
        ],
        "investigation_teams": [
            {
                "team_id": "infra",
                "tenant": "default",
                "environment": "prod",
                "enabled": True,
                "objective_prompt": "Infra team",
                "tool_allowlist": ["mcp.grafana.*", "mcp.prometheus.*"],
                "max_tool_calls": 2,
                "max_parallel_calls": 1,
                "timeout_seconds": 10,
                "updated_at": now,
                "updated_by": "test",
            }
        ],
        "team_missions": {
            "infra": {
                "team_id": "infra",
                "tenant": "default",
                "environment": "prod",
                "mission_objective": "Prove or rule out infra issues with real metrics and change context.",
                "required_checks": ["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
                "allowed_tools": ["mcp.grafana.*", "mcp.prometheus.*"],
                "completion_criteria": ["infra_completeness_reported"],
                "unknown_not_available_rules": ["missing_infra_signals", "missing_required_checks"],
                "relevance_weights": {"service_scoped": 1.0, "global": 0.8},
                "evidence_requirements": [
                    {
                        "evidence_class": "annotation_change_context",
                        "description": "check change context",
                        "tool_patterns": ["mcp.grafana.get_annotations"],
                        "query_scope": "change",
                        "required_symptoms": [],
                    },
                    {
                        "evidence_class": "local_service_metrics",
                        "description": "check service metrics",
                        "tool_patterns": ["mcp.prometheus.query_range", "mcp.prometheus.query_instant"],
                        "query_scope": "service",
                        "required_symptoms": ["latency", "resource"],
                    },
                    {
                        "evidence_class": "global_shared_metrics",
                        "description": "check shared metrics",
                        "tool_patterns": ["mcp.prometheus.query_range", "mcp.prometheus.query_instant"],
                        "query_scope": "global",
                        "required_symptoms": ["latency", "resource"],
                    },
                ],
                "symptom_overrides": {
                    "latency": ["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
                    "resource": ["annotation_change_context", "local_service_metrics", "global_shared_metrics"],
                },
                "updated_at": now,
                "updated_by": "test",
            }
        },
        "service_identity": {
            "canonical_service_id": "recommendation",
            "owner": None,
            "env": "prod",
            "dependency_graph_refs": [],
            "mapped_provider_ids": {"prometheus": "recommendation"},
            "confidence": 0.85,
            "ambiguous_candidates": [],
        },
        "resolver_artifact_state": {
            "alert_terms": ["recommendationservice"],
            "entity_terms": ["recommendationservice"],
            "explicit_service_terms": ["recommendationservice"],
            "title_terms": [],
            "summary_terms": [],
            "service_candidates": ["recommendation"],
            "resolved_service": "recommendation",
            "service_aliases": [],
            "operation_candidates": [],
            "resolved_operations": [],
            "trace_ids": [],
            "datasource_ids": [],
            "dashboard_uids": [],
            "annotation_tags": [],
            "metric_label_keys": [],
            "metric_service_candidates": [],
            "trace_summaries": [],
            "dependency_edges": [],
            "root_cause_signals": [],
        },
    }
    plan = {
        "investigation_id": investigation_id,
        "ordered_steps": [],
        "max_api_calls": 5,
        "max_stage_wall_clock_seconds": 600,
    }

    result = collect_evidence_stage(investigation_id, alert, plan, run_context)
    reports = {item["team_id"]: item for item in result["team_reports"]}
    assert reports["infra"]["completeness_status"] == "unknown_not_available"
    assert "missing_evidence_class:local_service_metrics" in reports["infra"]["unknown_not_available_reasons"]
    assert "missing_evidence_class:global_shared_metrics" in reports["infra"]["unknown_not_available_reasons"]


def test_emit_eval_event_uses_effective_execution_state() -> None:
    investigation_id = f"inv-{uuid4()}"
    report = {
        "top_hypotheses": [
            {
                "statement": "recommendation service cache failure path is causing latency",
                "confidence": 0.74,
                "supporting_citations": ["CIT-1"],
                "counter_evidence_citations": [],
            }
        ],
        "likely_cause": "recommendation path degraded",
        "blast_radius": "recommendation",
        "recommended_manual_actions": ["inspect cache behavior"],
        "confidence": 0.74,
    }
    evidence = [
        {
            "provider": "prometheus",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "evidence_type": "query_range",
            "normalized_fields": {"query": "service latency"},
            "citation_id": "CIT-1",
            "redaction_state": "clean",
        }
    ]
    run_context = {
        "agent_rollout_mode": "active",
        "effective_prompt_profiles": {"resolve_service_identity": {"model": "openai/mock"}},
        "effective_stage_missions": {"resolve_service_identity": {"mission_objective": "resolve"}},
        "effective_team_missions": {"infra": {"mission_objective": "prove infra healthy or unknown"}},
        "rerun_ledger": [
            {
                "sequence": 1,
                "requested_by_stage": "build_investigation_plan",
                "target_stage": "resolve_service_identity",
                "reason": "service_scope_invalid",
                "additional_objective": "anchor service identity",
                "expected_evidence": "resolved service",
                "tool_focus": ["mcp.jaeger.get_services"],
                "accepted": True,
                "outcome": "completed",
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        "alias_decision_trace": {
            "strategy": "anchor_priority",
            "selected_candidate": "recommendation",
            "matched_term": "recommendationservice",
            "matched_term_source": "entity_ids",
            "confidence": 0.84,
            "ambiguous_candidates": [],
            "top_candidates": [],
            "unresolved_reason": None,
        },
        "stage_eval_records": [
            {
                "stage_id": "resolve_service_identity",
                "record_id": "resolver_alias_quality",
                "status": "pass",
                "summary": "anchored to recommendation",
                "score": 0.84,
                "findings": [],
                "details": {},
            }
        ],
        "stage_results": {
            "resolve_service_identity": {
                "effective_tool_catalog_summary": {"allowed": ["mcp.jaeger.get_services"], "count": 1}
            }
        },
    }

    eval_event = emit_eval_event_stage(investigation_id, report, evidence, latency_seconds=12.5, run_context=run_context)
    assert eval_event["rollout_mode"] == "active"
    assert eval_event["alias_decision_trace"]["selected_candidate"] == "recommendation"
    assert eval_event["stage_eval_records"][0]["record_id"] == "resolver_alias_quality"
    assert eval_event["effective_prompt_snapshot"] == run_context["effective_prompt_profiles"]
    assert eval_event["eval_trace"]["training_artifact"]["rerun_ledger"] == run_context["rerun_ledger"]


def test_active_mode_invalid_route_raises_and_allows_strict_failure() -> None:
    alert = _sample_alert()
    invalid_run_context = {
        "tenant": "default",
        "environment": "prod",
        "agent_rollout_mode": "active",
        "llm_route": {"tenant": "default"},
    }
    with pytest.raises(Exception):
        resolve_service_stage(alert, invalid_run_context)


def test_compare_mode_captures_model_error_without_failing() -> None:
    alert = _sample_alert()
    run_context = {
        "tenant": "default",
        "environment": "prod",
        "agent_rollout_mode": "compare",
        "llm_route": {
            "tenant": "default",
            "environment": "prod",
            "primary_model": "codex",
            "fallback_model": "claude",
            "key_ref": "llm-provider-secret",
        },
        "mcp_servers": [],
        "mcp_tools": [],
    }

    result = resolve_service_stage(alert, run_context)
    assert result["agent_rollout_mode"] == "compare"
    assert result.get("model_error")
    assert "agent_compare" in result


def test_active_mode_model_failure_is_terminal() -> None:
    alert = _sample_alert()
    run_context = {
        "tenant": "default",
        "environment": "prod",
        "agent_rollout_mode": "active",
        "llm_route": {
            "tenant": "default",
            "environment": "prod",
            "primary_model": "codex",
            "fallback_model": "claude",
            "key_ref": "llm-provider-secret",
        },
        "mcp_servers": [],
        "mcp_tools": [],
    }

    with pytest.raises(RuntimeError):
        resolve_service_stage(alert, run_context)


def test_collect_evidence_rejects_non_mcp_plan_step() -> None:
    investigation_id = f"inv-{uuid4()}"
    alert = _sample_alert()
    plan = {
        "investigation_id": investigation_id,
        "ordered_steps": [
            {
                "provider": "otel",
                "rationale": "legacy connector step",
                "timeout_seconds": 60,
                "budget_weight": 1,
                "capability": "traces",
                "execution_source": "connector",
            }
        ],
        "max_api_calls": 1,
        "max_stage_wall_clock_seconds": 600,
    }

    with pytest.raises(Exception):
        collect_evidence_stage(investigation_id, alert, plan, {"execution_policy": "mcp_only"})


def test_collect_evidence_team_agents_best_effort_and_allowlists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.orchestrator.app.pipeline.summarize_with_model_route",
        _stub_summarize_with_model_route,
    )
    investigation_id = f"inv-{uuid4()}"
    alert = _sample_alert()
    now = datetime.now(timezone.utc).isoformat()

    def _stub_execute(servers: object, descriptor: object, arguments: object) -> tuple[dict, AgentToolTrace]:
        _ = servers
        _ = arguments
        started = datetime.now(timezone.utc)
        trace = AgentToolTrace(
            tool_name=f"mcp.{descriptor.server_id}.{descriptor.tool_name}",
            source="mcp",
            read_only=True,
            started_at=started,
            ended_at=started,
            duration_ms=5,
            success=True,
            args_summary={},
            result_summary={"ok": True},
            error=None,
            citations=[],
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": '{"signal":"observed","team_tool":"%s"}' % descriptor.tool_name,
                }
            ]
        }, trace

    monkeypatch.setattr("services.orchestrator.app.pipeline._execute_mcp_tool", _stub_execute)

    run_context = {
        "tenant": "default",
        "environment": "prod",
        "execution_policy": "mcp_only",
        "llm_route": {
            "tenant": "default",
            "environment": "prod",
            "primary_model": "openai/mock",
            "fallback_model": "openai/mock",
            "key_ref": "llm-provider-secret",
        },
        "mcp_servers": [
            {
                "server_id": "jaeger",
                "tenant": "default",
                "environment": "prod",
                "transport": "http_sse",
                "base_url": "http://jaeger-mcp:8000/mcp",
                "timeout_seconds": 8,
                "enabled": True,
                "updated_at": now,
                "updated_by": "test",
            },
            {
                "server_id": "grafana",
                "tenant": "default",
                "environment": "prod",
                "transport": "http_sse",
                "base_url": "http://grafana-mcp:8000/mcp",
                "timeout_seconds": 8,
                "enabled": True,
                "updated_at": now,
                "updated_by": "test",
            },
        ],
        "mcp_tools": [
            {
                "server_id": "jaeger",
                "tool_name": "search_traces",
                "description": "search traces",
                "capabilities": ["tracing"],
                "read_only": True,
                "light_probe": False,
                "arg_keys": ["service"],
                "required_args": ["service"],
            },
            {
                "server_id": "grafana",
                "tool_name": "get_annotations",
                "description": "get annotations",
                "capabilities": ["metrics"],
                "read_only": True,
                "light_probe": True,
                "arg_keys": [],
                "required_args": [],
            },
        ],
        "investigation_teams": [
            {
                "team_id": "app",
                "tenant": "default",
                "environment": "prod",
                "enabled": True,
                "objective_prompt": "App team",
                "tool_allowlist": ["mcp.jaeger.*"],
                "max_tool_calls": 3,
                "max_parallel_calls": 2,
                "timeout_seconds": 20,
                "updated_at": now,
                "updated_by": "test",
            },
            {
                "team_id": "infra",
                "tenant": "default",
                "environment": "prod",
                "enabled": True,
                "objective_prompt": "Infra team",
                "tool_allowlist": ["mcp.grafana.*"],
                "max_tool_calls": 3,
                "max_parallel_calls": 2,
                "timeout_seconds": 20,
                "updated_at": now,
                "updated_by": "test",
            },
            {
                "team_id": "db",
                "tenant": "default",
                "environment": "prod",
                "enabled": True,
                "objective_prompt": "DB team",
                "tool_allowlist": ["mcp.db.*"],
                "max_tool_calls": 3,
                "max_parallel_calls": 2,
                "timeout_seconds": 20,
                "updated_at": now,
                "updated_by": "test",
            },
        ],
    }
    plan = {
        "investigation_id": investigation_id,
        "ordered_steps": [
            {
                "provider": "mcp",
                "rationale": "tool-owned team evidence collection",
                "timeout_seconds": 60,
                "budget_weight": 1,
                "capability": "tracing",
                "execution_source": "mcp",
                "mcp_server_id": "jaeger",
                "mcp_tool_name": "search_traces",
            }
        ],
        "max_api_calls": 10,
        "max_stage_wall_clock_seconds": 600,
    }

    result = collect_evidence_stage(investigation_id, alert, plan, run_context)
    assert result["evidence"]
    assert result["mission_id"].startswith("stage:collect_evidence")
    assert isinstance(result.get("mission_checklist"), dict)
    assert len(result["team_execution"]) == 3
    by_team = {item["team_id"]: item for item in result["team_execution"]}
    assert by_team["app"]["status"] == "completed"
    assert by_team["infra"]["status"] == "completed"
    assert by_team["db"]["status"] == "skipped_no_tools"
    assert by_team["db"]["mission_checklist"]["mission_id"].startswith("team:db")
    assert "unknown_not_available_reasons" in by_team["db"]
    assert all("mcp.grafana." not in name for name in by_team["app"]["selected_tools"])
    assert all("mcp.jaeger." not in name for name in by_team["infra"]["selected_tools"])
    team_reports = {item["team_id"]: item for item in result["team_reports"]}
    assert team_reports["app"]["mission_id"].startswith("team:app")
    assert team_reports["infra"]["completeness_status"] in {"healthy", "unhealthy", "unknown_not_available"}


def test_infra_team_reports_unknown_when_required_checks_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.orchestrator.app.pipeline.summarize_with_model_route",
        _stub_summarize_with_model_route,
    )
    investigation_id = f"inv-{uuid4()}"
    alert = _sample_alert()
    now = datetime.now(timezone.utc).isoformat()
    run_context = {
        "tenant": "default",
        "environment": "prod",
        "execution_policy": "mcp_only",
        "llm_route": {
            "tenant": "default",
            "environment": "prod",
            "primary_model": "openai/mock",
            "fallback_model": "openai/mock",
            "key_ref": "llm-provider-secret",
        },
        "mcp_servers": [],
        "mcp_tools": [],
        "investigation_teams": [
            {
                "team_id": "infra",
                "tenant": "default",
                "environment": "prod",
                "enabled": True,
                "objective_prompt": "Infra team",
                "tool_allowlist": ["mcp.grafana.*"],
                "max_tool_calls": 2,
                "max_parallel_calls": 1,
                "timeout_seconds": 10,
                "updated_at": now,
                "updated_by": "test",
            }
        ],
        "team_missions": {
            "infra": {
                "team_id": "infra",
                "tenant": "default",
                "environment": "prod",
                "mission_objective": "Check infra health signals",
                "required_checks": ["infra_annotations_checked", "infra_latency_or_error_signal_checked"],
                "allowed_tools": ["mcp.grafana.*"],
                "completion_criteria": ["infra_completeness_reported"],
                "unknown_not_available_rules": ["missing_required_checks"],
                "relevance_weights": {"service_scoped": 1.0, "global": 0.8},
                "updated_at": now,
                "updated_by": "test",
            }
        },
    }
    plan = {
        "investigation_id": investigation_id,
        "ordered_steps": [],
        "max_api_calls": 5,
        "max_stage_wall_clock_seconds": 600,
    }

    result = collect_evidence_stage(investigation_id, alert, plan, run_context)
    reports = {item["team_id"]: item for item in result["team_reports"]}
    assert reports["infra"]["status"] == "skipped_no_tools"
    assert reports["infra"]["completeness_status"] == "unknown_not_available"
    assert reports["infra"]["mission_checklist"]["failed"] or reports["infra"]["mission_checklist"]["unavailable"]


def test_synthesis_includes_commander_arbitration_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    def _commander_stub(*_: object, **__: object) -> tuple[str, str]:
        return (
            "openai/mock",
            "\n".join(
                [
                    "H1: Recommendation cache stale data causes request retries.",
                    "H2: Infra latency increased in recommendation path.",
                    "CAUSE: recommendation cache saturation",
                    "ACTIONS: disable cache flag | flush cache",
                    "CONFLICTS: app vs infra confidence mismatch",
                    "DECISION_TRACE: preferred app hypothesis due to direct error traces",
                    "SELECTED_TEAMS: app,infra",
                ]
            ),
        )

    monkeypatch.setattr("services.orchestrator.app.pipeline.summarize_with_model_route", _commander_stub)
    alert = _sample_alert()
    service_identity = resolve_service_stage(alert)
    evidence_payload = [
        {
            "provider": "jaeger",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "evidence_type": "search_traces",
            "normalized_fields": {"content": [{"type": "text", "text": '{"trace":"error"}'}]},
            "citation_id": "CIT-1",
            "redaction_state": "clean",
        }
    ]
    collect_result = {
        "team_reports": [
            {
                "team_id": "app",
                "status": "completed",
                "summary": "app team found retried trace failures",
                "hypotheses": [
                    {
                        "statement": "cache issue",
                        "confidence": 0.81,
                        "supporting_citations": ["CIT-1"],
                        "counter_evidence_citations": [],
                    }
                ],
                "confidence": 0.81,
                "supporting_citations": ["CIT-1"],
                "unknowns": [],
                "tool_traces": [],
                "skipped_tools": [],
            }
        ],
        "team_execution": [
            {
                "team_id": "app",
                "status": "completed",
                "selected_tools": ["mcp.jaeger.search_traces"],
                "executed_tool_count": 1,
                "failed_tool_count": 0,
                "evidence_count": 1,
                "duration_ms": 10,
                "citations": ["CIT-1"],
                "error": None,
            }
        ],
    }

    synthesis = synthesize_report_stage(alert, service_identity, evidence_payload, collect_result=collect_result)
    assert synthesis["arbitration_conflicts"]
    assert synthesis["arbitration_decision_trace"]
    assert synthesis["synthesis_trace"]["selected_team_ids"] == ["app", "infra"]
