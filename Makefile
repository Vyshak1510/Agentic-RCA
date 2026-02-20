PYTHON ?= python3

.PHONY: setup test lint run-ingest run-analysis run-eval run-orchestrator-worker run-orchestrator-demo web-install web-dev

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
