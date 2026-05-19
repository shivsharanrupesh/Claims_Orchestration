# Makefile
# ─────────
# Common developer commands.
# Usage: make <target>

.PHONY: help install setup test test-unit test-integration eval lint format clean run docker-up docker-down deploy-infra

help:
	@echo ""
	@echo "Claims Orchestrator — Option B"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "  make install          Install Python dependencies"
	@echo "  make setup            First-time setup (bootstrap Azure resources)"
	@echo "  make run              Start all services locally (no Docker)"
	@echo "  make docker-up        Start via Docker Compose"
	@echo "  make docker-down      Stop Docker Compose"
	@echo ""
	@echo "  make test             Run all tests"
	@echo "  make test-unit        Run unit tests only"
	@echo "  make test-integration Run integration tests only"
	@echo "  make eval             Run decision quality eval harness"
	@echo ""
	@echo "  make lint             Check code style (ruff + mypy)"
	@echo "  make format           Auto-format code (black + ruff fix)"
	@echo "  make clean            Remove build artifacts"
	@echo ""
	@echo "  make deploy-infra     Deploy Azure infrastructure (Bicep)"
	@echo ""

install:
	pip install -e ".[dev]"

setup:
	@cp -n .env.example .env 2>/dev/null && echo "Created .env from .env.example" || echo ".env already exists"
	python scripts/bootstrap.py

run:
	python run.py

docker-up:
	docker compose up --build

docker-down:
	docker compose down

test:
	pytest tests/ -v --cov=src --cov-report=term-missing

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

eval:
	python -m tests.eval.run_eval \
		--eval-set data/eval/claims_eval.jsonl \
		--log-mlflow

eval-with-baseline:
	python -m tests.eval.run_eval \
		--eval-set data/eval/claims_eval.jsonl \
		--baseline data/eval/baseline_metrics.json \
		--output data/eval/latest_metrics.json \
		--log-mlflow

lint:
	ruff check src/ tests/
	mypy src/

format:
	black src/ tests/
	ruff check --fix src/ tests/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf dist/ build/ *.egg-info/ htmlcov/ .coverage

deploy-infra:
	@echo "Deploying Azure infrastructure..."
	@echo "Resource group: $(RG)"
	az deployment group create \
		--resource-group $(RG) \
		--template-file infrastructure/bicep/main.bicep \
		--parameters @infrastructure/bicep/parameters.dev.json.example
