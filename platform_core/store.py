from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import Lock

from platform_core.models import (
    AdjudicationRecord,
    AgentPromptProfile,
    AgentRolloutConfig,
    AgentRolloutMode,
    AlertEnvelope,
    ConnectorCredentialMode,
    ConnectorCredentialView,
    EvalRunResult,
    InvestigationRecord,
    InvestigationStatus,
    LlmProviderRoute,
    McpServerConfig,
    McpToolDescriptor,
    MappingUpsertRequest,
    StepAttempt,
    StepExecutionStatus,
    StepLogEntry,
    WorkflowLayoutState,
    WorkflowRunDetail,
    WorkflowRunEvent,
    WorkflowStageId,
)


class InMemoryStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self.investigations: dict[str, InvestigationRecord] = {}
        self.dedupe_index: dict[str, tuple[str, datetime]] = {}
        self.mappings: dict[tuple[str, str], MappingUpsertRequest] = {}
        self.llm_routes: dict[tuple[str, str], LlmProviderRoute] = {}
        self.connector_credentials: dict[tuple[str, str, str], ConnectorCredentialView] = {}
        self.mcp_servers: dict[tuple[str, str, str], McpServerConfig] = {}
        self.mcp_tools: dict[tuple[str, str, str], list[McpToolDescriptor]] = {}
        self.agent_prompt_profiles: dict[tuple[str, str, WorkflowStageId], AgentPromptProfile] = {}
        self.agent_rollout_configs: dict[tuple[str, str], AgentRolloutConfig] = {}
        self.workflow_layouts: dict[tuple[str, str, str], WorkflowLayoutState] = {}
        self.eval_runs: dict[str, EvalRunResult] = {}
        self.adjudications: list[AdjudicationRecord] = []
        self.counters: defaultdict[str, int] = defaultdict(int)

        self.runs_by_investigation: dict[str, list[str]] = defaultdict(list)
        self.run_details: dict[str, WorkflowRunDetail] = {}
        self.run_events: dict[str, list[WorkflowRunEvent]] = defaultdict(list)

        now = datetime.now(timezone.utc)
        self.llm_routes[("default", "prod")] = LlmProviderRoute(
            tenant="default",
            environment="prod",
            primary_model="codex",
            fallback_model="claude",
            key_ref="llm-provider-secret",
        )
        for stage_id in (WorkflowStageId.RESOLVE_SERVICE_IDENTITY, WorkflowStageId.BUILD_INVESTIGATION_PLAN):
            self.agent_prompt_profiles[("default", "prod", stage_id)] = AgentPromptProfile(
                tenant="default",
                environment="prod",
                stage_id=stage_id,
                system_prompt="You are an RCA investigation agent. Use read-only tools and cite evidence.",
                objective_template="Resolve alert {{incident_key}} with bounded, evidence-linked reasoning.",
                max_turns=4,
                max_tool_calls=6,
                tool_allowlist=[],
                updated_at=now,
                updated_by="system",
            )
        self.agent_rollout_configs[("default", "prod")] = AgentRolloutConfig(
            tenant="default",
            environment="prod",
            mode=AgentRolloutMode.COMPARE,
            updated_at=now,
            updated_by="system",
        )

    @staticmethod
    def _fingerprint(alert: AlertEnvelope) -> str:
        parts = [alert.incident_key, alert.severity, *sorted(alert.entity_ids)]
        return sha256("|".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _duration_ms(started_at: datetime | None, ended_at: datetime | None) -> int | None:
        if not started_at or not ended_at:
            return None
        return int((ended_at - started_at).total_seconds() * 1000)

    def record_alert(self, investigation: InvestigationRecord) -> tuple[str, bool]:
        with self._lock:
            fingerprint = self._fingerprint(investigation.alert)
            now = datetime.now(timezone.utc)
            if fingerprint in self.dedupe_index:
                existing_id, seen_at = self.dedupe_index[fingerprint]
                if now - seen_at <= timedelta(minutes=15):
                    return existing_id, True

            self.investigations[investigation.id] = investigation
            self.dedupe_index[fingerprint] = (investigation.id, now)
            self.counters["alerts_ingested_total"] += 1
            return investigation.id, False

    def get_investigation(self, investigation_id: str) -> InvestigationRecord | None:
        return self.investigations.get(investigation_id)

    def update_status(self, investigation_id: str, status: InvestigationStatus) -> InvestigationRecord | None:
        with self._lock:
            investigation = self.investigations.get(investigation_id)
            if not investigation:
                return None
            investigation.status = status
            investigation.latest_run_status = status
            investigation.updated_at = datetime.now(timezone.utc)
            investigation.timeline.append(f"Status updated to {status.value}")
            return investigation

    def list_investigations(
        self,
        status: InvestigationStatus | None = None,
        source: str | None = None,
        severity: str | None = None,
        tenant: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[InvestigationRecord], int]:
        _ = tenant  # Reserved for multi-tenant backend persistence integration.

        items = list(self.investigations.values())
        if status:
            items = [item for item in items if item.status == status]
        if source:
            items = [item for item in items if item.alert.source == source]
        if severity:
            items = [item for item in items if item.alert.severity == severity]

        items = sorted(items, key=lambda item: item.updated_at, reverse=True)
        total = len(items)
        offset = max(page - 1, 0) * page_size
        paginated = items[offset : offset + page_size]
        return paginated, total

    def create_run(
        self,
        investigation_id: str,
        run_id: str,
        workflow_id: str | None,
        started_by: str,
        started_at: datetime | None = None,
    ) -> WorkflowRunDetail | None:
        with self._lock:
            investigation = self.investigations.get(investigation_id)
            if not investigation:
                return None

            now = started_at or datetime.now(timezone.utc)
            run_detail = WorkflowRunDetail(
                run_id=run_id,
                investigation_id=investigation_id,
                workflow_id=workflow_id,
                status=InvestigationStatus.RUNNING,
                started_at=now,
                updated_at=now,
                started_by=started_by,
            )

            self.run_details[run_id] = run_detail
            self.run_events[run_id] = []
            self.runs_by_investigation[investigation_id].insert(0, run_id)

            investigation.active_run_id = run_id
            investigation.status = InvestigationStatus.RUNNING
            investigation.latest_run_status = InvestigationStatus.RUNNING
            investigation.updated_at = now
            investigation.timeline.append(f"Run {run_id} started")

            return run_detail

    def list_runs(self, investigation_id: str) -> list[WorkflowRunDetail]:
        run_ids = self.runs_by_investigation.get(investigation_id, [])
        return [self.run_details[run_id] for run_id in run_ids if run_id in self.run_details]

    def get_run(self, run_id: str) -> WorkflowRunDetail | None:
        return self.run_details.get(run_id)

    def get_run_for_investigation(self, investigation_id: str, run_id: str) -> WorkflowRunDetail | None:
        run = self.run_details.get(run_id)
        if not run or run.investigation_id != investigation_id:
            return None
        return run

    def get_active_run(self, investigation_id: str) -> WorkflowRunDetail | None:
        investigation = self.investigations.get(investigation_id)
        if not investigation or not investigation.active_run_id:
            return None
        return self.run_details.get(investigation.active_run_id)

    def set_run_workflow_id(self, run_id: str, workflow_id: str) -> WorkflowRunDetail | None:
        with self._lock:
            run = self.run_details.get(run_id)
            if not run:
                return None
            run.workflow_id = workflow_id
            run.updated_at = datetime.now(timezone.utc)
            return run

    def append_run_event(self, run_id: str, event: WorkflowRunEvent) -> WorkflowRunEvent | None:
        with self._lock:
            run = self.run_details.get(run_id)
            if not run:
                return None

            events = self.run_events[run_id]
            stored_event = event.model_copy(update={"event_index": len(events) + 1})
            events.append(stored_event)

            run.events_count = len(events)
            run.updated_at = stored_event.timestamp
            run.timeline.append(stored_event.message)

            if stored_event.workflow_id and not run.workflow_id:
                run.workflow_id = stored_event.workflow_id

            if stored_event.stage_id is not None:
                run.current_stage = stored_event.stage_id
                attempts = run.stage_attempts.setdefault(stored_event.stage_id, [])
                while len(attempts) < stored_event.attempt:
                    attempts.append(StepAttempt(attempt=len(attempts) + 1))

                attempt = attempts[stored_event.attempt - 1]
                attempt.status = stored_event.stage_status
                attempt.message = stored_event.message
                attempt.error = stored_event.error
                attempt.stage_reasoning_summary = stored_event.stage_reasoning_summary

                if stored_event.stage_status == StepExecutionStatus.RUNNING and not attempt.started_at:
                    attempt.started_at = stored_event.timestamp

                if stored_event.stage_status in {
                    StepExecutionStatus.COMPLETED,
                    StepExecutionStatus.FAILED,
                    StepExecutionStatus.SKIPPED,
                }:
                    if not attempt.ended_at:
                        attempt.ended_at = stored_event.timestamp
                    attempt.duration_ms = self._duration_ms(attempt.started_at, attempt.ended_at)

                seen = set(attempt.citations)
                for citation in stored_event.citations:
                    if citation not in seen:
                        attempt.citations.append(citation)
                        seen.add(citation)

                attempt.metadata.update(stored_event.metadata)
                if stored_event.tool_traces:
                    attempt.tool_traces = stored_event.tool_traces
                if stored_event.logs:
                    attempt.logs.extend(stored_event.logs)
                else:
                    attempt.logs.append(
                        StepLogEntry(
                            timestamp=stored_event.timestamp,
                            level="error" if stored_event.error else "info",
                            message=stored_event.message,
                        )
                    )

            if stored_event.run_status is not None:
                run.status = stored_event.run_status
            elif run.status == InvestigationStatus.PENDING:
                run.status = InvestigationStatus.RUNNING

            if run.status in {InvestigationStatus.COMPLETED, InvestigationStatus.FAILED}:
                run.ended_at = stored_event.timestamp
                run.duration_ms = self._duration_ms(run.started_at, run.ended_at)
            else:
                run.ended_at = None
                run.duration_ms = None

            investigation = self.investigations.get(run.investigation_id)
            if investigation:
                investigation.timeline.append(stored_event.message)
                investigation.updated_at = stored_event.timestamp
                investigation.status = run.status
                investigation.latest_run_status = run.status
                if run.status in {InvestigationStatus.COMPLETED, InvestigationStatus.FAILED}:
                    if investigation.active_run_id == run_id:
                        investigation.active_run_id = None
                else:
                    investigation.active_run_id = run_id

            return stored_event

    def list_run_events(self, run_id: str, cursor: int = 0) -> list[WorkflowRunEvent]:
        events = self.run_events.get(run_id, [])
        if cursor < 0:
            cursor = 0
        return [event for event in events if (event.event_index or 0) > cursor]

    def complete_run(self, run_id: str, message: str = "Workflow completed") -> WorkflowRunEvent | None:
        run = self.run_details.get(run_id)
        if not run:
            return None

        event = WorkflowRunEvent(
            run_id=run.run_id,
            investigation_id=run.investigation_id,
            workflow_id=run.workflow_id,
            stage_id=None,
            stage_status=StepExecutionStatus.COMPLETED,
            run_status=InvestigationStatus.COMPLETED,
            timestamp=datetime.now(timezone.utc),
            message=message,
        )
        return self.append_run_event(run_id, event)

    def fail_run(self, run_id: str, message: str) -> WorkflowRunEvent | None:
        run = self.run_details.get(run_id)
        if not run:
            return None

        event = WorkflowRunEvent(
            run_id=run.run_id,
            investigation_id=run.investigation_id,
            workflow_id=run.workflow_id,
            stage_id=None,
            stage_status=StepExecutionStatus.FAILED,
            run_status=InvestigationStatus.FAILED,
            timestamp=datetime.now(timezone.utc),
            message=message,
            error=message,
        )
        return self.append_run_event(run_id, event)

    def upsert_connector_credential(
        self,
        provider: str,
        tenant: str,
        environment: str,
        mode: ConnectorCredentialMode,
        updated_by: str,
        secret_ref_name: str | None = None,
        secret_ref_key: str | None = None,
        key_last4: str | None = None,
    ) -> ConnectorCredentialView:
        credential = ConnectorCredentialView(
            provider=provider,
            tenant=tenant,
            environment=environment,
            mode=mode,
            secret_ref_name=secret_ref_name,
            secret_ref_key=secret_ref_key,
            key_last4=key_last4,
            updated_at=datetime.now(timezone.utc),
            updated_by=updated_by,
        )
        key = (tenant, environment, provider)
        self.connector_credentials[key] = credential
        return credential

    def get_connector_credential(
        self,
        provider: str,
        tenant: str,
        environment: str,
    ) -> ConnectorCredentialView | None:
        return self.connector_credentials.get((tenant, environment, provider))

    def list_connector_credentials(
        self,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> list[ConnectorCredentialView]:
        creds = list(self.connector_credentials.values())
        if tenant:
            creds = [credential for credential in creds if credential.tenant == tenant]
        if environment:
            creds = [credential for credential in creds if credential.environment == environment]
        return sorted(creds, key=lambda credential: credential.updated_at, reverse=True)

    def get_llm_route(self, tenant: str, environment: str) -> LlmProviderRoute:
        route = self.llm_routes.get((tenant, environment))
        if route:
            return route
        return LlmProviderRoute(
            tenant=tenant,
            environment=environment,
            primary_model="codex",
            fallback_model="claude",
            key_ref="llm-provider-secret",
        )

    def upsert_mcp_server(self, config: McpServerConfig) -> McpServerConfig:
        key = (config.tenant, config.environment, config.server_id)
        self.mcp_servers[key] = config
        return config

    def list_mcp_servers(self, tenant: str, environment: str | None = None) -> list[McpServerConfig]:
        servers = [item for item in self.mcp_servers.values() if item.tenant == tenant]
        if environment:
            servers = [item for item in servers if item.environment == environment]
        return sorted(servers, key=lambda item: item.updated_at, reverse=True)

    def get_mcp_server(self, tenant: str, environment: str, server_id: str) -> McpServerConfig | None:
        return self.mcp_servers.get((tenant, environment, server_id))

    def set_mcp_tools(
        self,
        tenant: str,
        environment: str,
        server_id: str,
        tools: list[McpToolDescriptor],
    ) -> None:
        self.mcp_tools[(tenant, environment, server_id)] = tools

    def get_mcp_tools(self, tenant: str, environment: str, server_id: str) -> list[McpToolDescriptor]:
        return self.mcp_tools.get((tenant, environment, server_id), [])

    def list_all_mcp_tools(self, tenant: str, environment: str) -> list[McpToolDescriptor]:
        tools: list[McpToolDescriptor] = []
        for (tool_tenant, tool_env, _), entries in self.mcp_tools.items():
            if tool_tenant == tenant and tool_env == environment:
                tools.extend(entries)
        return tools

    def upsert_agent_prompt_profile(self, profile: AgentPromptProfile) -> AgentPromptProfile:
        key = (profile.tenant, profile.environment, profile.stage_id)
        self.agent_prompt_profiles[key] = profile
        return profile

    def list_agent_prompt_profiles(self, tenant: str, environment: str | None = None) -> list[AgentPromptProfile]:
        profiles = [item for item in self.agent_prompt_profiles.values() if item.tenant == tenant]
        if environment:
            profiles = [item for item in profiles if item.environment == environment]
        return sorted(profiles, key=lambda item: item.updated_at, reverse=True)

    def get_agent_prompt_profile(
        self,
        tenant: str,
        environment: str,
        stage_id: WorkflowStageId,
    ) -> AgentPromptProfile | None:
        return self.agent_prompt_profiles.get((tenant, environment, stage_id))

    def get_agent_rollout(self, tenant: str, environment: str) -> AgentRolloutConfig:
        rollout = self.agent_rollout_configs.get((tenant, environment))
        if rollout:
            return rollout
        return AgentRolloutConfig(
            tenant=tenant,
            environment=environment,
            mode=AgentRolloutMode.COMPARE,
            updated_at=datetime.now(timezone.utc),
            updated_by="system",
        )

    def upsert_agent_rollout(self, rollout: AgentRolloutConfig) -> AgentRolloutConfig:
        self.agent_rollout_configs[(rollout.tenant, rollout.environment)] = rollout
        return rollout

    def get_workflow_layout(self, tenant: str, user_id: str, workflow_key: str) -> WorkflowLayoutState | None:
        return self.workflow_layouts.get((tenant, user_id, workflow_key))

    def upsert_workflow_layout(self, layout: WorkflowLayoutState) -> WorkflowLayoutState:
        key = (layout.tenant, layout.user_id, layout.workflow_key)
        self.workflow_layouts[key] = layout
        return layout


store = InMemoryStore()
