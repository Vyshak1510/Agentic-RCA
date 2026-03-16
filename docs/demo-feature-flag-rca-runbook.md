# Demo Runbook: Generic Cart Error Alert -> RCA via Grafana + Jaeger MCP

This runbook reproduces a production-like flow: the alert only says cart error rate is high, and Agentic RCA infers likely cause using MCP tool evidence (Grafana + Jaeger), not explicit cause hints in alert payload.

## 1) Prerequisites

- Docker Desktop is running.
- Project root: `/Users/vyshak.r/Documents/Agentic-RCA`.
- Root `.env` includes at least:
  - `API_KEY=<your_api_key>`
  - `OPENAI_API_KEY=<your_openai_key>`
- OTel demo lives at: `third_party/opentelemetry-demo`.

## 2) Start Agentic RCA stack

```bash
cd /Users/vyshak.r/Documents/Agentic-RCA
docker compose -f docker-compose.local.yml up -d --build
```

Start MCP sidecars (Grafana + Jaeger):

```bash
docker compose \
  -f docker-compose.local.yml \
  -f docker-compose.grafana-mcp.local.yml \
  -f docker-compose.jaeger-mcp.local.yml \
  up -d --build grafana-mcp jaeger-mcp
```

Quick health checks:

```bash
curl -sS http://localhost:8000/v1/health
# expected: {"status":"ok","service":"ingest-api"}
```

Temporal UI:

- `http://localhost:8088`

## 3) Start OpenTelemetry demo

```bash
cd /Users/vyshak.r/Documents/Agentic-RCA/third_party/opentelemetry-demo
docker compose up -d
```

Useful local URLs (from current demo config):

- OTel storefront: `http://localhost:8085`
- Grafana: `http://localhost:3000`
- Jaeger UI: `http://localhost:16686`

## 4) Enable the failure scenario (cart failure)

Set only `cartFailure` to `on` for a clean signal:

```bash
cd /Users/vyshak.r/Documents/Agentic-RCA
jq '.flags.cartFailure.defaultVariant = "on" |
    .flags.productCatalogFailure.defaultVariant = "off" |
    .flags.recommendationCacheFailure.defaultVariant = "off" |
    .flags.kafkaQueueProblems.defaultVariant = "off"' \
  third_party/opentelemetry-demo/src/flagd/demo.flagd.json \
  > /tmp/demo.flagd.json && mv /tmp/demo.flagd.json third_party/opentelemetry-demo/src/flagd/demo.flagd.json

cd /Users/vyshak.r/Documents/Agentic-RCA/third_party/opentelemetry-demo
docker compose up -d flagd
```

## 5) Register MCP servers in Agentic RCA

```bash
cd /Users/vyshak.r/Documents/Agentic-RCA
make bootstrap-local-mcp
```

Expected output includes successful test responses and discovered tool counts for both servers.

## 6) Generate a bit of traffic (optional but helpful)

```bash
for i in {1..30}; do curl -sS http://localhost:8085 >/dev/null || true; done
```

## 7) Send alert to ingest-api

### Recommended generic payload (no explicit root-cause hints)

```json
{
  "source": "grafana",
  "severity": "critical",
  "incident_key": "demo-cart-high-error-20260312T161650Z",
  "entity_ids": ["cart"],
  "timestamps": {"triggered_at": "2026-03-12T16:16:50Z"},
  "raw_payload_ref": "demo://alert/cart/high-error",
  "raw_payload": {
    "title": "High error rate on cart service",
    "summary": "Cart service error rate exceeded threshold for 5 minutes",
    "service": "cart",
    "env": "local-demo"
  }
}
```

### Replay command

```bash
cd /Users/vyshak.r/Documents/Agentic-RCA
cat > /tmp/agentic_rca_demo_alert.json <<'JSON'
{
  "source": "grafana",
  "severity": "critical",
  "incident_key": "demo-cart-high-error-20260312T161650Z",
  "entity_ids": ["cart"],
  "timestamps": {"triggered_at": "2026-03-12T16:16:50Z"},
  "raw_payload_ref": "demo://alert/cart/high-error",
  "raw_payload": {
    "title": "High error rate on cart service",
    "summary": "Cart service error rate exceeded threshold for 5 minutes",
    "service": "cart",
    "env": "local-demo"
  }
}
JSON

curl -sS -X POST "http://localhost:8000/v1/alerts" \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  --data @/tmp/agentic_rca_demo_alert.json | tee /tmp/agentic_rca_demo_alert_response.json
```

## 8) Watch workflow execution and RCA output

```bash
INVESTIGATION_ID="$(jq -r '.investigation_id' /tmp/agentic_rca_demo_alert_response.json)"
RUN_ID="$(jq -r '.run_id' /tmp/agentic_rca_demo_alert_response.json)"

curl -sS "http://localhost:8000/v1/investigations/${INVESTIGATION_ID}/runs/${RUN_ID}" \
  -H "x-api-key: ${API_KEY}" | tee /tmp/agentic_rca_run_result.json

jq '{status, duration_ms, current_stage, timeline}' /tmp/agentic_rca_run_result.json
jq '.stage_attempts.synthesize_rca_report[0].metadata.report' /tmp/agentic_rca_run_result.json
```

## 9) Expected outcome from validated runs (2026-03-12)

- Run status: `completed`
- Duration: about `15.5s`
- Model used for synthesis: `openai/gpt-5.3-codex`
- Likely cause should be inferred from trace/evidence patterns in Jaeger/Grafana (for example cart mutation-path failures), not copied from alert payload text.
- Evidence providers: Jaeger traces + Grafana metadata
- Citations produced: 3

Validated IDs from runs:

- feature-flag-explicit run:
  - `investigation_id`: `77677c1e-8bb2-4065-9fff-58418997d763`
  - `run_id`: `run-c12415ec-e6b8-4033-83f0-645273bade80`
- generic-alert run (timestamp arg fix verified):
  - `investigation_id`: `64dcc572-4315-43fb-a572-5279eb8aa9ce`
  - `run_id`: `run-6e132bb4-5f8a-4789-95d5-10bf854ed90a`

## 10) Reset after demo

Set `cartFailure` back off and restart `flagd`:

```bash
cd /Users/vyshak.r/Documents/Agentic-RCA
jq '.flags.cartFailure.defaultVariant = "off"' \
  third_party/opentelemetry-demo/src/flagd/demo.flagd.json \
  > /tmp/demo.flagd.json && mv /tmp/demo.flagd.json third_party/opentelemetry-demo/src/flagd/demo.flagd.json

cd /Users/vyshak.r/Documents/Agentic-RCA/third_party/opentelemetry-demo
docker compose up -d flagd
```
