# Architecture

## Control Plane

- Kubernetes CRDs define connector, model route, policy, and eval gate state.
- Temporal orchestrates bounded investigation workflows with retry/time-budget control.

## Data Plane

1. `ingest-api` validates inbound alerts and normalizes to `AlertEnvelope`.
2. Resolver chain maps provider entities to canonical service identity.
3. Planner creates bounded `InvestigationPlan` from alert class templates.
4. Connector runtime executes read-only calls through core adapters or plugins.
5. Evidence pipeline redacts + normalizes to citation-backed `EvidenceItem`.
6. Analysis engine synthesizes top hypotheses with citation enforcement.
7. Publisher posts outputs to Slack/Jira.
8. Eval subsystem scores runs and enforces rollout gates.

## Resolver Chain

Order:

1. New Relic entity IDs/tags
2. Azure resource metadata/tags
3. Optional CMDB adapter
4. Optional RAG enrichment

Resolver emits confidence score plus ambiguous candidates.

## Guardrails

- Read-only connector policy for v1
- Hard max on API calls per incident
- Hard timeout per investigation stage
- Redaction-first before any LLM call
- Citation requirement for all RCA claims
