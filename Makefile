.PHONY: help install lint format test test-cov up down logs build clean dev worker docker-up docker-down docker-logs

# Prefer the active venv's binaries so `make test` / `make lint` work without
# juggling PATH. Falls back to the system ones if venv isn't there.
VENV_BIN := $(if $(wildcard .venv/bin),.venv/bin/,)

help:  ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:  ## Install runtime + dev dependencies into the active venv.
	pip install -r requirements.txt -r requirements-dev.txt

lint:  ## Run ruff lint checks.
	$(VENV_BIN)ruff check app/ tests/ scripts/

format:  ## Auto-format code with ruff.
	$(VENV_BIN)ruff format app/ tests/ scripts/
	$(VENV_BIN)ruff check --fix app/ tests/ scripts/

test:  ## Run the test suite.
	$(VENV_BIN)pytest tests/ -v

test-cov:  ## Run tests with coverage report.
	$(VENV_BIN)pytest tests/ --cov=app --cov-report=term-missing

# ---- Primary commands (Docker-based) ---------------------------------------

up:  ## Start the full stack (api + worker + postgres + redis) via Docker.
	docker compose up -d
	@echo ""
	@echo "✓ Stack is starting. API will be ready in a few seconds."
	@echo "  → API:        http://localhost:8000"
	@echo "  → API docs:   http://localhost:8000/docs"
	@echo "  → Health:     http://localhost:8000/health"
	@echo ""
	@echo "  Tail logs with:  make logs"

down:  ## Stop the stack and remove volumes (wipes the DB).
	docker compose down -v
	@echo "✓ Stack stopped and database volume removed."

logs:  ## Tail logs from all running services.
	docker compose logs -f

build:  ## Build the API + worker Docker images.
	docker compose build

# ---- Local Python (no Docker) -----------------------------------------------

dev:  ## Run the API locally with the in-memory store (no Docker needed).
	USE_IN_MEMORY_STORE=1 uvicorn app.main:app --reload

worker:  ## Run an RQ worker locally against a running Redis (no Docker needed).
	rq worker --url $${REDIS_URL:-redis://localhost:6379/0} $${RQ_QUEUE_NAME:-default}

# ---- Backward-compatible aliases -------------------------------------------

docker-up: up
docker-down: down
docker-logs: logs

# ---- Misc -------------------------------------------------------------------

clean:  ## Remove build/cache artifacts.
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov __pycache__
	find . -type d -name __pycache__ -exec rm -rf {} +
