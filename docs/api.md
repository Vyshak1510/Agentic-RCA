# Public API Summary

## Ingestion and Investigations

- `POST /v1/alerts`
- `POST /v1/alerts/newrelic`
- `GET /v1/investigations`
- `GET /v1/investigations/{id}`
- `GET /v1/investigations/{id}/events` (SSE)
- `POST /v1/investigations/{id}/runs`
- `GET /v1/investigations/{id}/runs`
- `GET /v1/investigations/{id}/runs/{run_id}`
- `GET /v1/investigations/{id}/runs/{run_id}/events` (SSE)
- `POST /v1/investigations/{id}/rerun`
- `POST /v1/internal/runs/events` (internal callback)
- `POST /v1/catalog/mappings/upsert`
- `POST /v1/providers/llm`

## Settings

- `GET /v1/me`
- `GET /v1/settings/connectors`
- `PUT /v1/settings/connectors/{provider}`
- `POST /v1/settings/connectors/{provider}/test`
- `GET /v1/settings/llm-routes`
- `PUT /v1/settings/llm-routes`
- `GET /v1/settings/mcp-servers`
- `PUT /v1/settings/mcp-servers/{server_id}`
- `POST /v1/settings/mcp-servers/{server_id}/test`
- `GET /v1/settings/mcp-servers/{server_id}/tools`
- `GET /v1/settings/agent-prompts`
- `PUT /v1/settings/agent-prompts/{stage_id}`
- `GET /v1/settings/agent-rollout`
- `PUT /v1/settings/agent-rollout`

## UI State

- `GET /v1/ui/workflow-layouts/{workflow_key}`
- `PUT /v1/ui/workflow-layouts/{workflow_key}`

## Evaluations

- `POST /v1/evals/runs`
- `GET /v1/evals/runs/{id}`
- `POST /v1/evals/adjudications`

## Ops

- `GET /v1/metrics`
- `GET /v1/health`
