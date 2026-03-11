PYTHON ?= python3

.PHONY: setup test lint run-ingest run-analysis run-eval run-orchestrator-worker run-orchestrator-demo web-install web-dev compose-up compose-down compose-logs compose-up-grafana-mcp compose-down-grafana-mcp compose-up-jaeger-mcp compose-down-jaeger-mcp compose-up-all-mcp compose-down-all-mcp bootstrap-local-mcp

setup:
	$(PYTHON) -m pip install -e .[dev]

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

run-ingest:
	$(PYTHON) services/ingest-api/app/run.py

run-analysis:
	$(PYTHON) services/analysis-engine/app/run.py

run-eval:
	$(PYTHON) services/eval-service/app/run.py

run-orchestrator-worker:
	$(PYTHON) services/orchestrator/app/worker.py

run-orchestrator-demo:
	$(PYTHON) services/orchestrator/app/run_demo.py

web-install:
	cd services/web-ui && npm install

web-dev:
	cd services/web-ui && npm run dev

compose-up:
	docker compose -f docker-compose.local.yml up -d --build

compose-down:
	docker compose -f docker-compose.local.yml down

compose-logs:
	docker compose -f docker-compose.local.yml logs -f --tail=200

compose-up-grafana-mcp:
	docker compose -f docker-compose.local.yml -f docker-compose.grafana-mcp.local.yml up -d --build grafana-mcp

compose-down-grafana-mcp:
	docker compose -f docker-compose.local.yml -f docker-compose.grafana-mcp.local.yml down grafana-mcp

compose-up-jaeger-mcp:
	docker compose -f docker-compose.local.yml -f docker-compose.jaeger-mcp.local.yml up -d --build jaeger-mcp

compose-down-jaeger-mcp:
	docker compose -f docker-compose.local.yml -f docker-compose.jaeger-mcp.local.yml down jaeger-mcp

compose-up-all-mcp:
	docker compose -f docker-compose.local.yml -f docker-compose.grafana-mcp.local.yml -f docker-compose.jaeger-mcp.local.yml up -d --build grafana-mcp jaeger-mcp

compose-down-all-mcp:
	docker compose -f docker-compose.local.yml -f docker-compose.grafana-mcp.local.yml -f docker-compose.jaeger-mcp.local.yml down grafana-mcp jaeger-mcp

bootstrap-local-mcp:
	./scripts/bootstrap_local_mcp_servers.sh
