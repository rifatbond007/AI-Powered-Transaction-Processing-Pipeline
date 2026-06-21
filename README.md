# AI-Powered Transaction Processing Pipeline

A FastAPI + RQ + Postgres service that ingests a messy `transactions.csv`,
runs a defensive ETL → anomaly detection → LLM classification → LLM
narrative on it asynchronously, and exposes the structured output via a
small REST API.

**At a glance**

| | |
|---|---|
| Stack | Python 3.12, FastAPI, SQLAlchemy 2, RQ, Postgres 16, Redis 7, Gemini 2.5 Flash |
| Bring-up | `make up` (full stack via docker-compose) |
| Tests | `make test` (~30 tests, < 5 s, no external services) |
| API docs | <http://localhost:8000/docs> after `make up` |
| Spec coverage | See [docs/SPEC_COMPLIANCE.md](docs/SPEC_COMPLIANCE.md) (~100% of PDF §4–§9) |

**Diagrams:** [§3 High-level flow](#3-architecture) ·
[§6.2 Data flow](#62-data-flow) ·
[§6.5 Scaling strategy](#65-scaling-strategy)

---

## Table of Contents

1. [What This Project Does](#1-what-this-project-does)
2. [Quick Start](#2-quick-start)
3. [Architecture](#3-architecture)
4. [API Contract](#4-api-contract)
5. [ETL Rules — Defensive by Design](#5-etl-rules--defensive-by-design)
6. [System Design & Scaling](#6-system-design--scaling)
   - [6.1 Topology & Lifecycle](#61-topology--lifecycle)
   - [6.2 Data Flow](#62-data-flow)
   - [6.3 Bottlenecks & Failure Modes](#63-bottlenecks--failure-modes)
   - [6.4 Capacity Estimates](#64-capacity-estimates)
   - [6.5 Scaling Strategy](#65-scaling-strategy)
   - [6.6 Observability](#66-observability)
7. [LLM Integration](#7-llm-integration)
8. [Design Decisions & Tradeoffs](#8-design-decisions--tradeoffs)
9. [Project Layout](#9-project-layout)
10. [Testing](#10-testing)
11. [DevOps & CI](#11-devops--ci)
12. [Roadmap](#12-roadmap)

**See also:**

- [docs/SPEC_COMPLIANCE.md](docs/SPEC_COMPLIANCE.md) — full PDF §4–§9 traceability matrix
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — topology, data flow, bottlenecks, scaling phases, capacity
- [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) — metrics, logs, traces, alerts, health checks
- [docs/ROADMAP.md](docs/ROADMAP.md) — 12 prioritized production improvements

---

## 1. What This Project Does

Given a messy `transactions.csv` with mixed date formats, currency symbols,
inconsistent casing, nulls, and duplicates, this service:

1. **Accepts the upload** via `POST /jobs/upload` and returns `202` + a `job_id`
   in milliseconds — actual work happens in the background.
2. **Cleans the data** defensively: bad rows go to a `quarantine` list with a
   reason, never silently dropped. Missing `category` is filled with the
   literal string `"Uncategorised"` per spec.
3. **Flags anomalies**: amount > 3× per-account median, OR USD paid to a
   domestic-only brand (Swiggy / Ola / IRCTC). Both rules can fire on the same
   row — reasons join with `+`.
4. **Classifies uncategorised rows** with the LLM in batches of 20, retried 3×.
5. **Generates a narrative summary** (total spend by currency, top 3 merchants,
   anomaly count, risk level) in one final LLM call.
6. **Exposes results** via `GET /jobs/{id}/results` once the job completes.
7. **Persists everything** to Postgres via SQLAlchemy; the store is the source
   of truth for job status — Redis/RQ is only a "ready to process" signal.

---

## 2. Quick Start

### Option A — Docker (recommended, ~30 seconds)

```bash
cp .env.example .env
# Optional: set GOOGLE_API_KEY=... in .env for real LLM calls
make up
```

Wait ~10 seconds, then:

```bash
curl http://localhost:8000/health                                # {"status":"ok"}
JOB=$(curl -sS -F file=@transactions.csv http://localhost:8000/jobs/upload | jq -r .job_id)
curl -sS http://localhost:8000/jobs/$JOB/status | jq .           # poll until completed/failed
curl -sS http://localhost:8000/jobs/$JOB/results | jq .          # transactions + summary
```

Interactive API docs: <http://localhost:8000/docs>. `make down` stops and
wipes the DB volume.

### Option B — Local Python (no Docker)

```bash
python3 -m venv .venv && source .venv/bin/activate
make install         # pip install -r requirements*.txt
make dev             # uvicorn app.main:app --reload  (SQLite, in-process)
```

Worker runs separately with `make worker` (requires Redis on `localhost:6379`).

### Without `GOOGLE_API_KEY`

Both LLM calls return `{"llm_failed": True}` after retries. The pipeline
**still completes** — ETL, anomaly detection, persistence, and a
deterministic-fallback summary all work.

---

## 3. Architecture

![High-level architecture diagram](docs/images/image.png)

*FastAPI edge tier enqueues jobs into Redis/RQ, workers run ETL → anomaly → LLM classify → LLM summarise → Postgres, with external Gemini calls.*

### Why this shape

- **HTTP layer / business logic / infrastructure are cleanly separated** —
  `routes/`, `services/`, `adapters/`. Swapping Redis for Kafka, or Postgres
  for SQLite, does not touch routes or business code.
- **JobStore is an ABC** (`app/adapters/storage.py`) with a single concrete
  `SqlJobStore` implementation. Tests use SQLite via `StaticPool`; production
  uses Postgres. Both share the same SQLAlchemy models.
- **The store is the source of truth for job status** — RQ is only used to
  signal "ready to process". A job can be cancelled, retried, or inspected
  by reading the DB directly, without parsing RQ internals.

---

## 4. API Contract

| Method | Path | Status Codes | Purpose |
|---|---|---|---|
| `GET`  | `/health` | 200 | Liveness probe |
| `POST` | `/jobs/upload` | 202 / 400 / 413 / 415 | Upload CSV, returns `job_id` |
| `GET`  | `/jobs` | 200 | List jobs (newest first; `?status=` filter; `?limit=&offset=`) |
| `GET`  | `/jobs/{id}/status` | 200 / 404 | Job state + summary (if completed) |
| `GET`  | `/jobs/{id}/results` | 200 / 404 / 409 | Full transactions + summary |

### Upload error semantics

| Code | When |
|---|---|
| `202` | Accepted; job enqueued |
| `400` | Empty file |
| `413` | File > 10 MiB (`MAX_UPLOAD_BYTES`) |
| `415` | Unsupported `Content-Type` (only `text/csv`, `application/csv`, `application/vnd.ms-excel`, `text/plain`, `application/octet-stream` accepted) |
| `503` | Failed to enqueue (Redis down) — the Job is marked `failed` so the client can see why |

---

## 5. ETL Rules — Defensive by Design

The pipeline (`app/services/etl.py`) **never silently drops** a row — every
rejected row appears in `CleanResult.quarantine` with a human-readable reason.

| Rule | Behaviour |
|---|---|
| **Date parsing** | Auto-detects `dd-mm-yyyy`, `yyyy/mm/dd`, `yyyy-mm-dd`, `dd/mm/yyyy`, ISO datetime |
| **Amounts** | Strips `$`, `€`, `£`, `¥`, commas, whitespace. Negative or zero → quarantine |
| **Currency** | Normalised to UPPERCASE. Empty → quarantine |
| **Status** | Normalised to UPPERCASE. Empty is allowed |
| **Missing `category`** | Filled with literal `"Uncategorised"` (per spec) |
| **Missing `txn_id`** | Regenerated as `TXN_GEN_<row_index>` |
| **Missing `account_id`** | Quarantined |
| **Unparseable date / amount** | Quarantined with the offending raw value in the reason |
| **Duplicates** | Detected on `(txn_id, date, amount, account_id)`, quarantined |

Two anomaly rules apply (`app/services/anomaly.py`): `amount_3x_median`
(amount > 3× median for the same `account_id`) and `usd_domestic` (USD paid
to Swiggy / Ola / IRCTC). Both can fire — reasons join with `+`.

---

## 6. System Design & Scaling

A condensed view of how the system behaves under load. Full detail in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### 6.1 Topology & Lifecycle

| Tier | Component | Role | Stateful? |
|---|---|---|---|
| Edge | **FastAPI** (`api` service) | Accepts uploads, creates `Job`, enqueues worker task, serves status/results reads | Stateless |
| Queue | **Redis 7** + **RQ** | Buffers "ready to process" signals between API and workers | Yes (volatile) |
| Compute | **RQ Worker** (`worker` service) | Runs `process_job`: ETL → anomaly → LLM classify → LLM summarise → persist | Stateless |
| Storage | **Postgres 16** | Source of truth for jobs, transactions, summaries | Yes (durable) |
| External | **Gemini 2.5 Flash** | LLM calls for classify + summary | Third-party |

Request lifecycle: `POST /jobs/upload` → 202 in ~50 ms → ETL + anomaly
(<1 s) → LLM classify (N/20 batches, ~1–3 s each) → persist (<500 ms) →
LLM summary (~1–5 s) → status=completed. **5–15 s end-to-end** for a 1k-row
CSV with 100 uncategorised rows, dominated by LLM latency.

### 6.2 Data Flow

![Data flow — end to end](docs/images/all-stage.png)

A single CSV upload traverses eight stages: ingress → queue → ETL → anomaly →
LLM classify → persist transactions → LLM summarise → persist summary. Each
stage has a clear input/output contract (see [docs/ARCHITECTURE.md §3](docs/ARCHITECTURE.md#3-data-flow--end-to-end)).

**Read path** (client polling `/jobs/{id}/status` or `/results`) is just
`SELECT … FROM jobs / transactions / job_summaries WHERE id = ?` — sub-10ms
on Postgres.

### 6.3 Bottlenecks & Failure Modes

Top bottlenecks, in rough order of likelihood in production:

1. **LLM classify latency** — serial Gemini calls; 100 uncategorised rows = 5 × 1–3s = 5–15s.
   Today: batching (20/call). At scale: parallelise, cache merchant→category.
2. **Gemini API rate limits** — free-tier quotas. Today: tenacity retries. At scale: multi-provider + circuit breaker.
3. **Worker single-process ETL** — `pd.read_csv` blocks; 100k rows ≈ 5–10s CPU. At scale: stream-parse, dedicated ETL worker pool.
4. **Postgres bulk INSERT** — 100k rows in one transaction. At scale: `COPY` protocol, chunked commits.
5. **Redis SPOF for queue** — workers stall if Redis dies. At scale: Sentinel / Cluster, or Postgres-backed queue (`SKIP LOCKED`).

Failure modes already handled per PDF §5(e):

- LLM classify failure → rows marked `llm_failed=True`, job completes ✅
- LLM summary failure → deterministic fallback narrative ✅
- ETL error → job marked `failed`, `error_message` stored ✅
- Worker crash → RQ re-enqueues after visibility timeout; idempotent re-run ✅
- Redis restart → queue rebuilds from in-flight jobs ✅

Full 10-row bottleneck table at [docs/ARCHITECTURE.md §4](docs/ARCHITECTURE.md#4-bottlenecks--failure-modes).

### 6.4 Capacity Estimates

| Workload | Jobs/day | API QPS (peak) | Worker count | Postgres size / month | Notes |
|---|---|---|---|---|---|
| **Demo / interview** | ~10 | <1 | 1 | <1 MB | Single VM |
| **Small team** | 100 | ~5 | 2 | ~30 MB | One VM, Postgres + Redis |
| **Mid-market** | 10,000 | ~50 | 10–20 | ~3 GB | Multi-VM, Redis Sentinel, read replica |
| **Enterprise** | 1,000,000 | ~500 | 200+ | ~300 GB | Kafka, S3, multi-region |

Storage: 1 transaction row ≈ 200 bytes; 1 summary row ≈ 2 KB. At enterprise
scale, partition `transactions` by `created_at` with 90-day retention.

### 6.5 Scaling Strategy

![Scaling strategy: four phases from single-host to event-sourced](docs/images/scalling.png)

| Phase | Scale | Key change |
|---|---|---|
| **1 — Single host** (today) | ~100 jobs/day | API + worker + Postgres + Redis on one VM |
| **2 — Horizontal workers** | ~1k jobs/day | `docker compose up --scale worker=N`; split CPU-bound ETL workers from network-bound LLM workers |
| **3 — Multi-host prod** | ~100k jobs/day | N API replicas behind LB; Redis Sentinel; managed Postgres with read replicas; S3 for uploads; `COPY` for bulk inserts; Redis cache for status polls |
| **4 — Event-sourced** | 1M+ jobs/day | Kafka topics per stage; streaming ETL; outbox pattern; per-tenant LLM routing; AsyncAPI contracts |

Full phase detail at [docs/ARCHITECTURE.md §5](docs/ARCHITECTURE.md#5-scaling-strategy).

### 6.6 Observability

Prometheus metrics, structured `structlog` logs, OpenTelemetry traces across
API → RQ → worker → LLM → DB. Alert examples: job failure rate spike,
worker queue depth growing, LLM failure rate, API p99 latency, Postgres
connection pool exhaustion.

Full metric catalogue, log fields, trace IDs, and alert conditions at
[docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).

---

## 7. LLM Integration

- **Provider**: Gemini 2.5 Flash via `google-genai` (free tier, no spend).
  Configure with `GOOGLE_API_KEY`.
- **Batch size**: 20 rows per `classify_categories` call
  (`LLM_BATCH_SIZE=20`, env-overridable).
- **Retry**: 3 attempts, exponential backoff (1s, 2s, 4s) via `tenacity`.
- **Failure isolation**: a failed batch marks all rows `llm_failed=True`; the
  job still completes — only ETL/DB/IO errors mark the job `failed`. The
  summary falls back to a rule-based narrative + risk level
  (`high` if >3 anomalies, `medium` if >0, else `low`).

Provider isolated to `app/services/llm.py` — swapping is one file.

---

## 8. Design Decisions & Tradeoffs

| Decision | Rationale |
|---|---|
| **RQ + Redis (not Celery)** | RQ is pure-Python, simpler, and matches the single-queue topology. Celery's broker / result-backend complexity is overkill here. |
| **Async, job-based (not synchronous)** | LLM calls + ETL can take seconds. Returning a `job_id` and letting the client poll is the right UX. |
| **`JobStore` ABC + SQLAlchemy ORM** | Routes and worker don't care about storage. Tests use SQLite; production uses Postgres. |
| **Store as source of truth (not RQ)** | Job state lives in Postgres. Cancelling / retrying / inspecting a job = `SELECT * FROM jobs WHERE id = ?`. |
| **Pydantic v2** | 5–50× faster than v1, better type inference, native discriminated unions. |
| **No Alembic** | Out of scope for the assignment. `Base.metadata.create_all()` is fine for fresh DBs. Production would add Alembic with a baseline migration. |
| **SQLAlchemy parameter binding everywhere** | No f-string SQL. Injection-safe by construction. |
| **Tenacity for retries** | Production-grade retry lib with explicit attempt counts and backoff. |

---

## 9. Project Layout

```
.
├── app/
│   ├── main.py                # FastAPI app + lifespan
│   ├── config.py              # Pydantic settings (env-driven)
│   ├── database.py            # SQLAlchemy engine + session factory
│   ├── models.py              # ORM: Job, Transaction, JobSummary
│   ├── schemas.py             # Pydantic request/response models
│   ├── dependencies.py        # FastAPI DI
│   ├── adapters/              # Swappable infrastructure (queue, storage)
│   ├── routes/                # HTTP layer (health, jobs)
│   └── services/              # Business logic: etl, anomaly, llm, fx, upload, worker
├── scripts/entrypoint.py      # Container entrypoint: wait for DB/Redis, ensure schema
├── tests/                     # ~30 pytest tests, no external services required
├── docs/                      # ARCHITECTURE, SPEC_COMPLIANCE, OBSERVABILITY, ROADMAP + images/
├── .github/workflows/ci.yml   # Lint + test (coverage threshold) + Docker build
├── Dockerfile                 # Multi-stage, non-root, healthcheck
├── docker-compose.yml         # api + worker + postgres + redis + pgadmin
├── Makefile                   # Common dev commands
├── pyproject.toml             # ruff + pytest config
├── requirements*.txt          # Runtime + dev deps
├── .env.example               # Env template
└── transactions.csv           # Sample data
```

---

## 10. Testing

```bash
make test         # full suite (~30 tests, < 5 s, no services)
make test-cov     # with coverage report
make lint         # ruff check + format check
```

**Test design choices:**

- **No real services in unit tests.** SQLite-in-memory via `StaticPool`,
  `fakeredis` for the queue, `monkeypatch` for env vars. Fast, deterministic,
  no Docker required.
- **Real services in CI.** `.github/workflows/ci.yml` spins up `postgres:16-alpine`
  and `redis:7-alpine` as service containers.
- **LLM is always mocked** in tests — `_classify_call` and `_summarize_call`
  are patched at the module level.

Test modules map 1:1 to spec sections.

---

## 11. DevOps & CI

### Container

- **Multi-stage build** — builder installs deps into a venv, runtime copies the
  venv and runs as non-root `appuser` (uid 1000).
- **Healthcheck** — Dockerfile `HEALTHCHECK` pings `/health` every 30 s.
- **`docker-compose.yml`** — `api` + `worker` + `postgres` + `redis` +
  `pgadmin`. Worker shares the `uploads` named volume with the API.
- **Entrypoint** — `scripts/entrypoint.py` blocks on TCP for Postgres and
  Redis, ensures the schema exists (creates tables only if missing), then
  `exec`s the CMD.

### CI (`.github/workflows/ci.yml`)

Two jobs:

1. **`test`** — `ruff check`, `ruff format --check`, `pytest --cov=app --cov-fail-under=70`
   against Postgres + Redis service containers.
2. **`docker-build`** — Builds the API image with Buildx; uses GHA cache.
   Runs only after `test` passes.

Triggers: `push` and `pull_request` to `main`.

---

## 12. Roadmap

Top production improvements (full list at [docs/ROADMAP.md](docs/ROADMAP.md)):

1. `InMemoryJobStore` for unit tests + a true "no DB" `make dev` mode
2. Alembic migrations with a baseline
3. Idempotent upload endpoint (dedupe by file hash)
4. Quarantine exposure in `/jobs/{id}/results`
5. Per-job cancellation (`DELETE /jobs/{id}`)
6. Structured JSON logs (`structlog`) + request-id correlation

---

## License

For interview evaluation only.