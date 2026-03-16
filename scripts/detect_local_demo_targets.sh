#!/usr/bin/env bash

set -eu

detect_container_url() {
  container_name="$1"
  container_port="$2"
  default_url="$3"

  raw_port="$(docker port "$container_name" "$container_port" 2>/dev/null | head -n 1 || true)"
  if [ -z "$raw_port" ]; then
    printf '%s' "$default_url"
    return 0
  fi

  host_port="${raw_port##*:}"
  if [ -z "$host_port" ]; then
    printf '%s' "$default_url"
    return 0
  fi

  printf 'http://host.docker.internal:%s' "$host_port"
}

export GRAFANA_MCP_TARGET_URL="${GRAFANA_MCP_TARGET_URL:-$(detect_container_url grafana 3000/tcp http://host.docker.internal:3000)}"
export JAEGER_MCP_TARGET_URL="${JAEGER_MCP_TARGET_URL:-$(detect_container_url jaeger 16686/tcp http://host.docker.internal:16686)}"
export PROMETHEUS_MCP_TARGET_URL="${PROMETHEUS_MCP_TARGET_URL:-$(detect_container_url prometheus 9090/tcp http://host.docker.internal:9090)}"
