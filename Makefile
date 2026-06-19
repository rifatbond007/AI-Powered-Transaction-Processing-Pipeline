.PHONY: help install lint format test test-cov up down logs build clean dev docker-up docker-down docker-logs

help:  ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:  ## Install runtime + dev dependencies into the active venv.
	pip install -r requirements.txt -r requirements-dev.txt

lint:  ## Run ruff lint checks.
	ruff check app/ tests/ scripts/

format:  ## Auto-format code with ruff.
	ruff format app/ tests/ scripts/
	ruff check --fix app/ tests/ scripts/

test:  ## Run the test suite.
	pytest tests/ -v

test-cov:  ## Run tests with coverage report.
	pytest tests/ --cov=app --cov-report=term-missing

# ---- Primary commands (Docker-based) ---------------------------------------

up:  ## Start the full stack (api + postgres + redis) via Docker.
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

build:  ## Build the API Docker image.
	docker compose build

# ---- Local Python (no Docker) -----------------------------------------------

dev:  ## Run the API locally with the in-memory store (no Docker needed).
	USE_IN_MEMORY_STORE=1 uvicorn app.main:app --reload

# ---- Backward-compatible aliases -------------------------------------------

docker-up: up
docker-down: down
docker-logs: logs

# ---- Misc -------------------------------------------------------------------

clean:  ## Remove build/cache artifacts.
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov __pycache__
	find . -type d -name __pycache__ -exec rm -rf {} +
