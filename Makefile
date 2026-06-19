.PHONY: help install lint format test test-cov run docker-build docker-up docker-down docker-logs clean

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

run:  ## Run the API locally (in-memory store).
	USE_IN_MEMORY_STORE=1 uvicorn app.main:app --reload

docker-build:  ## Build the API Docker image.
	docker compose build

docker-up:  ## Start the full stack (api + postgres + redis).
	docker compose up -d
	@echo "API will be ready in a few seconds at http://localhost:8000"

docker-down:  ## Stop the stack and remove volumes (wipes DB).
	docker compose down -v

docker-logs:  ## Tail logs from all services.
	docker compose logs -f

clean:  ## Remove build/cache artifacts.
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov __pycache__
	find . -type d -name __pycache__ -exec rm -rf {} +