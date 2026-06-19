# PUKU.md

This file provides guidance to puku-cli when working with code in this repository.

## Stack

Python 3.11 / FastAPI / SQLAlchemy 2.x / Pydantic v2 / Pandas / Postgres 16 / Redis 7 / **RQ (Redis Queue)** / **Gemini 1.5 Flash (google-generativeai)** / **tenacity** / ruff / pytest / Docker Compose. Pinned in `requirements.txt`.

The canonical spec is `Backend_DevOps_Assignment.pdf`. The job-based async pipeline in `app/worker.py` is the source of truth for behavior. `@instruction.md` describes an older synchronous design and is **not** authoritative for new work.

## Build & run

- `make install` — runtime + dev deps into active venv
- `make lint` / `make format` — ruff
- `make test` / `make test-cov` — pytest (uses SQLite + fakeredis, no services needed)
- `make up` — **api + worker + postgres + redis** via docker compose
- `make dev` — uvicorn --reload with in-memory store, no Docker needed (also runs the API; the worker still needs Docker for real runs)
- `make worker` — run `rq worker` locally against a local Redis
- `make help` — list all targets

## Endpoints

All under `/jobs`. JSON in / JSON out.

- `POST /jobs/upload` — multipart `file` (≤10 MiB). Returns **202** with `{job_id, filename, status, row_count_raw, created_at}`. Enqueues an RQ job.
- `GET /jobs/{job_id}/status` — 200 with `{job_id, status, row_count_raw, row_count_clean, created_at, completed_at, error_message}`. **404** if unknown.
- `GET /jobs/{job_id}/results` — 200 with `{job, transactions, summary, llm_failures}`. **404** unknown, **409** if not `completed`, **500** if summary missing.
- `GET /jobs` — 200 with `{items, total}`. Query params: `limit` 1–200 (default 50), `offset ≥ 0`.

Plus `GET /health`.

## Worker pipeline (`app/worker.py::process_job`)

`JobStore.set_job_status("processing")` → `run_etl` (clean only, fill missing `category` with `"Uncategorised"`) → `flag_anomalies` (3× account-median rule + USD-domestic rule) → for rows still `Uncategorised`, `llm.classify_categories` batched at `LLM_BATCH_SIZE=20`, retried 3× with exponential backoff (1s/2s/4s) → `JobStore.attach_transactions` → build deterministic summary → `llm.generate_summary` (single call, also retried) → `JobStore.attach_summary` → `set_job_status("completed", row_count_clean=N)` → `upload.cleanup` in `finally`.

**Failure semantics:** uncaught exception → `set_job_status("failed", error=...)`, re-raised so RQ logs the traceback. **LLM batch failures do not fail the job** (PDF §5(e)) — only `llm_failed=True` on the affected rows.

## Key conventions

- Storage is pluggable: `JobStore` ABC (`app/storage.py`) with `InMemoryJobStore` and `SqlJobStore`. Routes use `Depends(get_job_store)`. Set `USE_IN_MEMORY_STORE=1` (or `APP_ENV=test`) to force the in-memory backend.
- The store is the **single source of truth** for job status. RQ is only used to signal "ready to process", never to store authoritative state.
- Lifespan will reuse a pre-registered store (so tests can register one in a fixture without it being clobbered).
- All SQL goes through SQLAlchemy parameter binding. Never f-string SQL.
- ETL rules (date format auto-detect, currency symbol/uppercase normalization, duplicate detection, missing-account-id/date quarantine, `Uncategorised` fill for empty category, `$` amount stripping) are authoritative in `Backend_DevOps_Assignment.pdf` §5(a).
- Anomaly rules (`app/anomaly.py`): amount > 3× the per-account median **OR** USD paid to a domestic brand (`Swiggy`, `Ola`, `IRCTC`). Both can fire on the same row (reasons joined with `+`). Single-row accounts skip rule A (median equals value).

## Style

- Ruff config in `pyproject.toml`: line-length 100, target py311, rules `E F W I B UP N SIM RUF` (E501 ignored). Per-file ignores in `[tool.ruff.lint.per-file-ignores]`. Don't change style settings without team discussion.
- Type hints on public functions; Google-style docstrings on modules and public functions; no `print()` in app code (use `logging`); no wildcard imports.
- Use `datetime.now(UTC)` (not the deprecated `datetime.utcnow()`).
- `LLM_BATCH_SIZE` defaults to 20 — don't tune it without a measurement.

## LLM

- `app/llm.py` is the only place `google.generativeai` is imported (inside `_call_gemini`). Tests monkeypatch `_classify_call` / `_summarize_call`; never call Gemini for real in tests.
- If `GOOGLE_API_KEY` is unset, both calls return `{"llm_failed": True}` after exhausting retries — the worker handles this and the job still completes.
- Free-tier Gemini (1.5 Flash) is the only supported provider. No spend allowed.

## API rules

- Query params for `GET /jobs`: `limit` 1–200 (default 50), `offset ≥ 0`.
- `transactions` in results are ordered `date DESC, id ASC`.
- 404 returns `{"detail": "..."}`. 409 returns `{"detail": {"message": "job not ready", "status": "..."}}`. Amounts in summary are in INR (via `app/fx.py::to_inr` with static rates).
- In docker-compose, Postgres is on host port `5433` and Redis on `6380` (container 5432/6379 are remapped to avoid host collisions). When running locally against docker-compose, use those host ports in `DATABASE_URL` / `REDIS_URL`.

## Testing

- Local: `pytest tests/ -v` (no services needed — uses SQLite + fakeredis).
- The autouse `_env` fixture in `test_jobs_api.py` registers a fresh `InMemoryJobStore` so `TestClient(app)` does not clobber test state.
- Tests live alongside the modules they cover under `tests/`; fixtures in `tests/conftest.py` (`sample_csv_path`, `real_csv_path`, `in_memory_store`).
- Run a single test: `pytest tests/test_worker_pipeline.py::test_name -v`.

## Container entrypoint

`scripts/entrypoint.py` waits for Postgres/Redis TCP, runs `init_db` only when the schema is empty, then execs the CMD (api container: `uvicorn app.main:app`; worker container: `rq worker --url redis://redis:6379/0 default`). Both containers share the `uploads` named volume (`UPLOAD_DIR=/tmp/uploads`) so the worker can clean up after the API.