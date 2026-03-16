#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
ENVIRONMENT="${ENVIRONMENT:-prod}"

API_KEY_VAL="${API_KEY:-}"
if [[ -z "$API_KEY_VAL" && -f "$ROOT_DIR/.env" ]]; then
  API_KEY_VAL="$(grep '^API_KEY=' "$ROOT_DIR/.env" | cut -d= -f2- || true)"
fi

curl_auth=()
if [[ -n "$API_KEY_VAL" ]]; then
  curl_auth=(-H "x-api-key: $API_KEY_VAL")
fi

curl_json=(
  -sS
  -H "content-type: application/json"
)
if [[ -n "$API_KEY_VAL" ]]; then
  curl_json+=(-H "x-api-key: $API_KEY_VAL")
fi

curl_call() {
  curl -sS "$@"
}

curl_json_call() {
  curl_call "${curl_json[@]}" "$@"
}

curl_json_call -X PUT "$API_BASE_URL/v1/settings/mcp-servers/grafana" \
  -d "{\"tenant\":\"default\",\"environment\":\"$ENVIRONMENT\",\"transport\":\"http_sse\",\"base_url\":\"http://grafana-mcp:8000/mcp\",\"secret_ref_key\":\"GRAFANA_MCP_API_KEY\",\"timeout_seconds\":12,\"enabled\":true}" >/dev/null

curl_json_call -X PUT "$API_BASE_URL/v1/settings/mcp-servers/jaeger" \
  -d "{\"tenant\":\"default\",\"environment\":\"$ENVIRONMENT\",\"transport\":\"http_sse\",\"base_url\":\"http://jaeger-mcp:8000/mcp\",\"timeout_seconds\":12,\"enabled\":true}" >/dev/null

curl_json_call -X PUT "$API_BASE_URL/v1/settings/mcp-servers/prometheus" \
  -d "{\"tenant\":\"default\",\"environment\":\"$ENVIRONMENT\",\"transport\":\"http_sse\",\"base_url\":\"http://prometheus-mcp:8000/mcp\",\"timeout_seconds\":12,\"enabled\":true}" >/dev/null

GRAFANA_TEST="$(curl_json_call -X POST "$API_BASE_URL/v1/settings/mcp-servers/grafana/test?environment=$ENVIRONMENT")"
JAEGER_TEST="$(curl_json_call -X POST "$API_BASE_URL/v1/settings/mcp-servers/jaeger/test?environment=$ENVIRONMENT")"
PROMETHEUS_TEST="$(curl_json_call -X POST "$API_BASE_URL/v1/settings/mcp-servers/prometheus/test?environment=$ENVIRONMENT")"
GRAFANA_TOOLS="$(curl_json_call "$API_BASE_URL/v1/settings/mcp-servers/grafana/tools?environment=$ENVIRONMENT")"
JAEGER_TOOLS="$(curl_json_call "$API_BASE_URL/v1/settings/mcp-servers/jaeger/tools?environment=$ENVIRONMENT")"
PROMETHEUS_TOOLS="$(curl_json_call "$API_BASE_URL/v1/settings/mcp-servers/prometheus/tools?environment=$ENVIRONMENT")"

python3 - "$GRAFANA_TEST" "$JAEGER_TEST" "$PROMETHEUS_TEST" "$GRAFANA_TOOLS" "$JAEGER_TOOLS" "$PROMETHEUS_TOOLS" <<'PY'
import json
import sys

grafana_test = json.loads(sys.argv[1])
jaeger_test = json.loads(sys.argv[2])
prometheus_test = json.loads(sys.argv[3])
grafana_tools = json.loads(sys.argv[4]).get("items", [])
jaeger_tools = json.loads(sys.argv[5]).get("items", [])
prometheus_tools = json.loads(sys.argv[6]).get("items", [])

print("Grafana MCP test:", grafana_test)
print("Jaeger MCP test:", jaeger_test)
print("Prometheus MCP test:", prometheus_test)
print("Grafana tools discovered:", len(grafana_tools))
print("Jaeger tools discovered:", len(jaeger_tools))
print("Prometheus tools discovered:", len(prometheus_tools))
PY
