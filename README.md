# RCA Agent Platform

Cloud-agnostic, Kubernetes-first, Apache-2.0 open-source platform for alert-driven root-cause analysis (RCA).

## v1 Product Boundaries

- RCA-only output (top-3 hypotheses + evidence + confidence)
- No autonomous remediation
- Read-only investigations against observability/platform connectors
- Slack + Jira publishing supported
- Formal rollout gates enforced by eval subsystem

## Success Criteria

- p95 first RCA latency under 10 minutes
- Top-3 hit rate >= 65%
- Top-1 hit rate >= 35%
- Unsupported-claim rate < 5%
- Shadow mode before assisted mode
- 100% human review for first 2 weeks

## Tech Defaults

- Runtime: Python
- Deployment: Kubernetes only
- Orchestration: Temporal
- Data: PostgreSQL + Redis
- Artifacts: S3-compatible object store (MinIO default)
- Auth: API key default, optional OIDC
- License: Apache-2.0

## Architecture

Core services:

- `ingest-api`: source-agnostic alert ingestion and normalization
- `orchestrator-worker`: Temporal workflows and bounded investigation plans
- `resolver-service`: canonical service/API/env identity resolution
- `connector-runtime`: read-only connector execution
- `evidence-store`: citation-backed normalized evidence storage
- `analysis-engine`: LLM synthesis with primary/fallback routing
- `publisher`: Slack/Jira publication
- `eval-service`: replay runs, online scoring, rollout gates
- `policy-service`: read-only and data egress/redaction policy checks

## Repository Layout

- `services/ingest-api`
- `services/orchestrator`
- `services/analysis-engine`
- `services/eval-service`
- `connectors/core/newrelic`
- `connectors/core/azure`
- `connectors/core/otel`
- `sdk/plugin-sdk-python`
- `charts/rca-platform`
- `crds/`
- `evals/golden-datasets/`
- `examples/`

## APIs

- `POST /v1/alerts`
- `POST /v1/alerts/newrelic`
- `GET /v1/investigations/{id}`
- `POST /v1/investigations/{id}/rerun`
- `POST /v1/catalog/mappings/upsert`
- `POST /v1/providers/llm`
- `POST /v1/evals/runs`
- `GET /v1/evals/runs/{id}`
- `POST /v1/evals/adjudications`
- `GET /v1/metrics`
- `GET /v1/health`

## Kubernetes Assets

- Helm chart: `charts/rca-platform`
- CRDs:
  - `ConnectorConfig`
  - `ModelRoute`
  - `InvestigationPolicy`
  - `CatalogSource`
  - `EvalPolicy`

## Local Quick Start

```bash
make setup
make test
```

## Temporal Local Demo

1. Start Temporal:

```bash
docker compose -f infra/temporal/docker-compose.yml up -d
```

2. Run one end-to-end workflow execution (worker + trigger in one process):

```bash
make run-orchestrator-demo
```

The workflow executes these v1 RCA stages:

1. Resolve canonical service identity
2. Build and validate bounded investigation plan
3. Collect connector evidence with early-stop logic
4. Synthesize citation-backed top-3 hypotheses (primary/fallback LLM routing)
5. Publish Slack/Jira summary (optional)
6. Emit eval/adjudication event metadata

If your local Python env does not have `temporalio`, run the same demo in a disposable container:

```bash
docker run --rm --network temporal_default \
  -e TEMPORAL_ADDRESS=temporal:7233 \
  -e PYTHONPATH=/app \
  -v "$PWD":/app -w /app \
  python:3.11-slim sh -lc "pip install --no-cache-dir temporalio pydantic >/dev/null && python services/orchestrator/app/run_demo.py"
```

## Delivery Status

This repository currently contains a production-oriented baseline scaffold for v1, including:

- Service contracts and API skeletons
- Connector SDK interfaces and contract tests
- CRDs + Helm packaging baseline
- Evaluation contracts, sample golden dataset, and CI gate workflow
