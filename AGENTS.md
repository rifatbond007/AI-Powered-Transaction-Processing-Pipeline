# AGENTS.md

## Architecture

**Job-queue-based async pipeline** (`app/worker.py:process_job` is the source of truth). `instruction.md` describes an older synchronous design — it is **not** authoritative.

`POST /jobs/upload` → 202 + RQ enqueue → worker runs: `run_etl` (clean only) → `flag_anomalies` (3x-median + USD-domestic) → `llm.classify_categories` (batched at 20, retried 3×) → persist → `llm.generate_summary` (retried 3×) → persist.

LLM batch failures DO NOT fail the job — only `llm_failed=True` on affected rows. Uncaught exceptions mark the job `failed`.

## Storage

Pluggable `JobStore` ABC (`app/storage.py`). The store is the single source of truth for job status, not the RQ queue.

- `USE_IN_MEMORY_STORE=1` or `APP_ENV=test` → `InMemoryJobStore`
- otherwise → `SqlJobStore` (Postgres via SQLAlchemy)

Tests set `deps._store = store` directly to bypass FastAPI DI.

## Endpoints

| Method | Path | Notes |
|--------|------|-------|
| GET | `/health` | Liveness probe |
| POST | `/jobs/upload` | Multipart `file` (≤10 MiB). Returns **202** |
| GET | `/jobs` | Query: `limit`(1-200), `offset`(≥0), `status` filter |
| GET | `/jobs/{id}/status` | Includes `summary` when `completed` |
| GET | `/jobs/{id}/results` | **404** unknown, **409** not ready, **200** done |

## Commands

```bash
make install   # pip install -r requirements.txt -r requirements-dev.txt
make lint      # ruff check app/ tests/ scripts/
make format    # ruff format + ruff check --fix
make test      # pytest tests/ -v  (35 tests, no services needed)
make test-cov  # pytest --cov=app --cov-report=term-missing
make dev       # USE_IN_MEMORY_STORE=1 uvicorn app.main:app --reload
make up        # docker compose up -d (api + worker + postgres + redis)
make down      # docker compose down -v (wipes DB volume)
```

Single test: `pytest tests/test_worker_pipeline.py::test_name -v`

## Testing quirks

- No real services needed: uses **SQLite** + **fakeredis** + **InMemoryJobStore**
- `conftest.py` autosets `APP_ENV=test`, `DATABASE_URL=sqlite:///:memory:`, `USE_IN_MEMORY_STORE=1`
- API tests patch `app.queue.enqueue_process_job` to run the worker synchronously inline
- LLM tests patch `_classify_call` / `_summarize_call` (private functions), not the public ones
- `test_jobs_api.py` has an autouse `_env` fixture that registers a fresh `InMemoryJobStore`

## Docker port mapping

Host `5433` → container `5432` (Postgres). Host `6380` → container `6379` (Redis). When running locally against docker-compose, use those host ports in `DATABASE_URL` / `REDIS_URL`.

## Style / conventions

- **Ruff** (config in `pyproject.toml`): line-length 100, target py311, double quotes. Rules: `E F W I B UP N SIM RUF` (E501 ignored). Per-file ignores in `[tool.ruff.lint.per-file-ignores]`.
- `datetime.now(UTC)` — never `datetime.utcnow()`
- Type hints on all public functions; Google-style docstrings; no `print()` (use `logging`); no wildcard imports
- All SQL via SQLAlchemy parameter binding — never f-string SQL
- Anomaly reasons join multiple rules with `+` (e.g. `amount_3x_median+usd_domestic`)
- Domestic-only brands: `Swiggy`, `Ola`, `IRCTC`
- Static FX rates in `app/fx.py` — no real API
- `LLM_BATCH_SIZE` defaults to 20 — don't tune without measurement

## Entrypoints

- **FastAPI**: `app/main.py:create_app()` (factory)
- **Worker**: `app/worker.py:process_job(job_id, csv_path)` (RQ task)
- **Container**: `scripts/entrypoint.py` waits for TCP, ensures schema, execs CMD

## Project layout

```
app/              # Application code
  main.py         # FastAPI app + lifespan
  config.py       # Pydantic Settings (env-driven)
  database.py     # SQLAlchemy engine + session
  models.py       # ORM models (Job, Transaction, JobSummary)
  schemas.py      # Pydantic request/response models
  etl.py          # ETL cleaning (not full pipeline)
  anomaly.py      # 3x-median + USD-domestic rules
  llm.py          # Gemini client (batched, retried)
  queue.py        # RQ helpers
  upload.py       # CSV upload lifecycle
  worker.py       # process_job — full pipeline orchestrator
  fx.py           # Static exchange rates
  storage.py      # JobStore ABC + InMemoryJobStore + SqlJobStore
  dependencies.py # FastAPI DI helpers
  routes/         # health.py, jobs.py
scripts/          # entrypoint.py, init_db.py
tests/            # 35 tests, conftest.py
```
