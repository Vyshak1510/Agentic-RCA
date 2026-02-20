# Public API Summary

## Ingestion and Investigations

- `POST /v1/alerts`
- `POST /v1/alerts/newrelic`
- `GET /v1/investigations/{id}`
- `POST /v1/investigations/{id}/rerun`
- `POST /v1/catalog/mappings/upsert`
- `POST /v1/providers/llm`

## Evaluations

- `POST /v1/evals/runs`
- `GET /v1/evals/runs/{id}`
- `POST /v1/evals/adjudications`

## Ops

- `GET /v1/metrics`
- `GET /v1/health`
