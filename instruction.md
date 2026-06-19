# Project Instructions — Backend DevOps Assignment

This document defines the rules, conventions, and workflow that **must be followed** throughout the development of this assignment. It is the single source of truth for the AI assistant and the candidate.

---

## 1. Project Goal

Build a **production-grade backend service** that:

1. Ingests a messy `transactions.csv` file via a robust **ETL pipeline**.
2. Exposes the cleaned data through a **REST API** (FastAPI).
3. Persists data in **PostgreSQL** with **Redis** caching.
4. Runs as a fully containerized service via **Docker Compose**.
5. Ships with **CI/CD** (GitHub Actions) and clear **documentation**.

The solution must demonstrate clean code, defensive data handling, and operational thinking — not just happy-path functionality.

---

## 2. Technology Stack (Locked)

| Layer        | Choice                                      |
| ------------ | ------------------------------------------- |
| Language     | Python 3.11+ (project targets 3.11–3.13)    |
| Web framework| **FastAPI**                                 |
| Data         | **Pandas**                                  |
| ORM          | **SQLAlchemy 2.x**                          |
| Validation   | **Pydantic v2**                             |
| DB           | **PostgreSQL 16**                           |
| Cache        | **Redis 7**                                 |
| Migrations   | **Alembic**                                 |
| Tests        | **pytest** + **httpx** (for API tests)      |
| Lint/Format  | **ruff**                                    |
| Container    | **Docker** (multi-stage) + Docker Compose   |
| CI           | **GitHub Actions**                          |

Do **not** substitute alternatives unless explicitly approved. Reasons are documented in the README tradeoffs section.

---

## 3. Folder Structure (Required)

```
backend-devops-assignment/
├── app/
│   ├── __init__.py
│   ├── main.py                # FastAPI entrypoint
│   ├── config.py              # Pydantic settings (env-driven)
│   ├── database.py            # SQLAlchemy engine + session
│   ├── models.py              # ORM models
│   ├── schemas.py             # Pydantic request/response models
│   ├── cache.py               # Redis client wrapper
│   ├── etl.py                 # ETL pipeline
│   └── routes/
│       ├── __init__.py
│       ├── transactions.py
│       └── summary.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py            # Fixtures (DB, client, sample CSV)
│   ├── test_etl.py
│   └── test_api.py
├── scripts/
│   └── init_db.py             # Loads CSV → Postgres
├── .github/
│   └── workflows/
│       └── ci.yml
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── .gitignore
├── .env.example
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml             # ruff config
├── README.md
└── instruction.md             # this file
```

---

## 4. ETL Rules (Non-Negotiable)

The source CSV is intentionally dirty. The pipeline **must** handle:

### 4.1 Dates
- Two formats coexist: `dd-mm-yyyy` and `yyyy/mm/dd` and `yyyy-mm-dd`.
- Parser must **auto-detect** format per row.
- Output is always `YYYY-MM-DD` (ISO 8601) in a `date` column.
- Unparseable dates → row is flagged in a `quarantine` list, not dropped silently.

### 4.2 Amounts
- Some values have a `$` prefix (e.g. `$11325.79`).
- Some are plain floats.
- Strip currency symbols, commas, whitespace. Convert to `Decimal` then `float` for the API.
- Negative or zero amounts → quarantine.

### 4.3 Currency Normalization
- Values may be `INR`, `inr`, `Inr` → must normalize to **uppercase**.
- Valid set: `{INR, USD, EUR, GBP}`. Anything else → quarantine.
- Convert all amounts to **INR** using:
  - USD → INR: `83.2`
  - EUR → INR: `90.5`
  - GBP → INR: `107.8`
- A new column `amount_inr` is added.

### 4.4 Status Normalization
- `SUCCESS`, `success`, `Success` → `SUCCESS`
- `FAILED`, `failed` → `FAILED`
- `PENDING`, `pending` → `PENDING`
- Anything else → quarantine.

### 4.5 Nulls / Missing Identifiers
- Rows missing `txn_id` are **regenerated** as `TXN_GEN_<row_index>` and logged.
- Rows missing `account_id` → quarantine.
- Missing `merchant`, `category`, `notes` → empty string (not NaN) in the API.

### 4.6 Duplicates
- A row is a duplicate if `(txn_id, date, amount, account_id)` matches an earlier row.
- Keep the **first** occurrence; log the rest to `quarantine` with reason `duplicate`.

### 4.7 Suspicious Flag
- A transaction is marked `is_suspicious = True` if:
  - `amount_inr > 100_000`, **or**
  - `notes` contains `SUSPICIOUS` (case-insensitive).
- Exposed via `GET /suspicious`.

### 4.8 Output
The ETL function returns:
```python
{
  "clean_df": pd.DataFrame,
  "quarantine": list[dict],   # rejected rows with reason
  "summary": dict             # counts, totals, by_category, by_status
}
```

---

## 5. API Rules

| Endpoint | Method | Rules |
| -------- | ------ | ----- |
| `/transactions` | GET | Filters: `start_date`, `end_date`, `status`, `category`, `account_id`, `currency`. **Pagination** via `limit` (default 50, max 500) and `offset`. |
| `/transactions/{txn_id}` | GET | 404 if not found. |
| `/summary` | GET | Cached in Redis for **60 seconds**. Key: `summary:v1`. |
| `/suspicious` | GET | Returns all `is_suspicious = True` rows. Same pagination as `/transactions`. |
| `/health` | GET | Returns `{"status": "ok"}`. Used by Docker + CI. |

- All responses are JSON.
- Errors use standard HTTP codes with `{"detail": "..."}` body.
- Dates in query strings accept `YYYY-MM-DD`.
- All amounts in responses are in **INR** (already converted by ETL).

---

## 6. Database Rules

- One table: `transactions` with columns matching the cleaned DataFrame.
- Indexes on: `txn_id` (unique), `date`, `account_id`, `status`, `category`.
- Use **Alembic** for schema migrations. Initial migration checked in.
- `is_suspicious` is a generated/computed column OR set in ETL — pick one and document.
- DB connection via env vars: `DATABASE_URL`.

---

## 7. Caching Rules

- Redis URL via `REDIS_URL` env var.
- Only `/summary` is cached (TTL = 60s).
- Cache failures must **not** break the API — log and fall through to DB.

---

## 8. Docker Rules

- **Multi-stage** Dockerfile:
  - `builder` stage installs requirements into a venv.
  - `runtime` stage copies only the venv + app code.
- Base image: `python:3.11-slim`.
- Non-root user (`appuser`, uid 1000).
- `HEALTHCHECK` hitting `/health`.
- `docker-compose.yml` services: `api`, `postgres`, `redis`.
- `api` depends_on `postgres` (healthy) and `redis` (healthy).
- A `wait-for-it` or in-app retry handles DB readiness (no shell hacks).

---

## 9. CI/CD Rules (GitHub Actions)

Single workflow `ci.yml` triggered on `push` and `pull_request` to `main`:

1. **Lint**: `ruff check .` and `ruff format --check .`
2. **Test**: `pytest --cov=app --cov-report=term-missing` (fail if coverage < 70%)
3. **Docker build**: `docker build` (no push — keeps CI fast and free).

Use a Postgres + Redis service container for the test job.

---

## 10. Testing Rules

- Minimum **8 unit tests** for `etl.py` covering: date parsing, currency conversion, status normalization, null `txn_id`, duplicates, suspicious flag, quarantine, summary generation.
- Minimum **4 API tests** for the routes (happy path + 404 + filter + pagination).
- Use `pytest` fixtures; no network calls in unit tests (mock DB with SQLite in-memory if needed, or use the testcontainers pattern with the CI Postgres).
- Tests must run in **< 30 seconds** total.

---

## 11. Code Style Rules

- **Ruff** for linting and formatting. Config in `pyproject.toml`.
- Line length: **100**.
- Type hints on **all** public functions.
- Docstrings (Google style) on modules and public functions.
- No `print()` in app code — use `logging`.
- No wildcard imports (`from x import *`).
- Prefer composition over inheritance.

---

## 12. Git Workflow Rules

- Repository is initialized **once** in Segment 1.
- Each **segment** = one logical chunk = one commit (or a small set of well-named commits).
- Commit message format: `segment-N: <short description>` (e.g. `segment-2: add ETL pipeline with currency conversion`).
- The AI assistant handles `git add` / `git commit` / `git push` using the user-provided remote.
- **Never** force-push to `main`. **Never** commit secrets (`.env` is in `.gitignore`).
- The user reviews each segment on GitHub before the next one starts.

---

## 13. Environment Variables (`.env.example`)

```
DATABASE_URL=postgresql+psycopg2://postgres:postgres@postgres:5432/transactions
REDIS_URL=redis://redis:6379/0
APP_ENV=development
LOG_LEVEL=INFO
```

For local dev (outside Docker) use `localhost` instead of service names.

---

## 14. Definition of Done (per segment)

A segment is "done" only when:

- [ ] Code compiles / app starts without errors.
- [ ] Relevant tests pass.
- [ ] No new lint errors.
- [ ] Files are committed and pushed to GitHub.
- [ ] The user has confirmed the segment on GitHub before moving on.

---

## 15. Out of Scope (for this assignment)

- Authentication / authorization.
- Rate limiting.
- Horizontal scaling, k8s manifests.
- Real currency exchange API (rates are static per spec).
- Frontend / dashboard.

These can be mentioned in the README "Future Work" section but are **not** implemented.

---

## 16. Communication Rules

- The AI assistant will **announce** when a segment is complete and **wait** for the user's go-ahead before starting the next.
- If the user requests changes within a segment, the AI will apply them, re-run tests, and re-push — all within the same segment number (no jumping ahead).
- If the AI is unsure about a design choice, it will **ask before assuming**.

---

_End of instructions._
