# Architecture

## Control Plane

- Kubernetes CRDs define connector, model route, policy, and eval gate state.
- Temporal orchestrates bounded investigation workflows with retry/time-budget control.

## Data Plane

1. `ingest-api` validates inbound alerts and normalizes to `AlertEnvelope`.
   - Includes convenience webhook adapters for `newrelic` and `grafana` payloads.
2. Resolver stage supports compare/active rollout:
   - Compare mode: deterministic resolver remains active while agentic resolver runs in parallel and records diffs.
   - Active mode: agentic resolver output is source of truth; stage failure fails run.
3. Planner stage supports compare/active rollout:
   - Compare mode: deterministic planner remains active while agentic planner runs in parallel and records diffs.
   - Active mode: agentic planner output is source of truth; stage failure fails run.
4. Evidence collection executes MCP-only plan steps in v1 MCP mode (`mcp.grafana.*`, `mcp.jaeger.*`); non-MCP steps are rejected.
5. Evidence pipeline redacts + normalizes to citation-backed `EvidenceItem`.
6. Analysis engine synthesizes top hypotheses with citation enforcement.
7. Publisher posts outputs to Slack/Jira.
8. Eval subsystem scores runs and enforces rollout gates.

## Agentic Runtime

- Model routing uses tenant/environment LLM route settings (`primary_model` + `fallback_model`).
- Stage prompts are configurable per stage (`resolve_service_identity`, `build_investigation_plan`).
- Tool registry merges:
  - Built-in connector tools
  - MCP-discovered tools
- Planning stage uses light probes only (no deep evidence reads).
- Tool-call traces are stored as sanitized summaries (no secret values).
- Tool selection is argument-aware: MCP tools with unmet required args are skipped and recorded as `skipped_tools`.
- Resolver/planner run metadata includes `requested_model`, `resolved_model`, and `model_error`.

## MCP and Control Surfaces

- MCP servers are managed via settings API and can be connection-tested.
- MCP tool catalogs are fetched and cached per tenant/environment/server.
- Agent rollout mode is configurable (`compare`, `active`).
- Web mapper layout state is persisted per tenant+user+workflow key.

## Resolver Chain

Order:

1. New Relic entity IDs/tags
2. Azure resource metadata/tags
3. Optional CMDB adapter
4. Optional RAG enrichment

Resolver emits confidence score plus ambiguous candidates.

## Guardrails

- Read-only MCP policy for v1 (MCP-only execution mode)
- Hard max on API calls per incident
- Hard timeout per investigation stage
- Redaction-first before any LLM call
- Citation requirement for all RCA claims
