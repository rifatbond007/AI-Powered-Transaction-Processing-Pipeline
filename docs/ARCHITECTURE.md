# Architecture & Scaling

Deep dive into how the system is shaped, how data flows through it, where
it breaks under load, and how to scale it from a single VM to enterprise
volume.

For the high-level overview, see the [README §3 Architecture](../README.md#3-architecture).
For observability specifics, see [OBSERVABILITY.md](OBSERVABILITY.md).

---

## Table of Contents

1. [Topology](#1-topology)
2. [Request → Response Lifecycle](#2-request--response-lifecycle)
3. [Data Flow — End to End](#3-data-flow--end-to-end)
4. [Bottlenecks & Failure Modes](#4-bottlenecks--failure-modes)
5. [Scaling Strategy](#5-scaling-strategy)
6. [Capacity Estimates](#6-capacity-estimates)
7. [Scaling Knobs Already in Codebase](#7-scaling-knobs-already-in-codebase)

---

## 1. Topology

A classic three-tier async pipeline.

| Tier | Component | Role | Stateful? |
|---|---|---|---|
| Edge | **FastAPI** (`api` service) | Accepts uploads, creates `Job`, enqueues worker task, serves status/results reads | Stateless |
| Queue | **Redis 7** + **RQ** | Buffers "ready to process" signals between API and workers | Yes (volatile) |
| Compute | **RQ Worker** (`worker` service) | Runs `process_job`: ETL → anomaly → LLM classify → LLM summarise → persist | Stateless |
| Storage | **Postgres 16** | Source of truth for jobs, transactions, summaries | Yes (durable) |
| External | **Gemini 2.5 Flash** | LLM calls for classify + summary | Third-party |

### Why this shape

- **HTTP front, worker back** — the API never blocks on LLM calls or ETL. A
  10 MiB CSV upload returns `202` in milliseconds; the heavy lifting happens
  out-of-band.
- **Queue is a signal, not a state store** — losing Redis doesn't lose jobs,
  because the DB row is created *before* enqueue. Workers re-hydrate state
  from Postgres on every task.
- **Store is the source of truth for status** — RQ's internal job state is
  irrelevant for the API contract; we only ever read job status from
  `SELECT status FROM jobs WHERE id = ?`. This makes status reads cheap,
  consistent, and trivially auditable.
- **Pure services** (`etl.py`, `anomaly.py`, `llm.py`) — no DB / network / I/O
  in the business logic except where explicitly required. Easy to reason
  about, easy to unit test.

---

## 2. Request → Response Lifecycle

```
T+0ms      client → POST /jobs/upload
T+~5ms     API: create Job row (status=pending), stream upload to disk
T+~50ms    API: count raw rows, patch Job
T+~55ms    API: enqueue process_job → return 202 {job_id}
T+~60ms    Worker pops task → set status=processing
T+~60ms    ETL (pd.read_csv + cleaning)        [CPU, in-process]
T+~200ms   Anomaly detection (groupby)         [CPU, in-process]
T+~200ms   LLM classify — N/20 batches        [NETWORK, serial]
T+~5–30s   LLM summary — 1 call               [NETWORK]
T+~5.5s    Persist transactions + summary     [DB, batch INSERT]
T+~5.5s    Set status=completed
```

For a 1,000-row CSV with 100 uncategorised rows, typical total job time is
**5–15 seconds end-to-end**, dominated by LLM latency.

---

## 3. Data Flow — End to End

A single CSV upload traverses these stages. Each stage has a clear input/output
contract, which is what makes the system debuggable.

![Data flow — end to end](images/all-stage.png)

**Read path** (client polling `/jobs/{id}/status` or `/results`) is just
`SELECT … FROM jobs / transactions / job_summaries WHERE id = ?` — sub-10ms
on Postgres for any single job.

### Stage contracts

| Stage | Where | Input → Output | Latency budget |
|---|---|---|---|
| 1. Ingress | API process | CSV stream → `Job` row + raw count | <100 ms |
| 2. Queue | Redis 7 | Job ID + csv_path | <10 ms |
| 3. ETL | worker, in-memory | CSV → `CleanResult {rows, quarantine, row_count_raw}` | 100–500 ms |
| 4. Anomaly | worker, in-memory | rows → rows w/ `is_anomaly` + `anomaly_reason` | <100 ms |
| 5. LLM classify | worker, network | uncategorised rows → rows w/ `llm_category` | 1–3 s/batch |
| 6. Persist transactions | worker, DB | rows → `transactions` table | 50–500 ms |
| 7. LLM summarise | worker, network | aggregate payload → `JobSummary` | 1–5 s |
| 8. Persist summary + done | worker, DB | summary → `job_summaries` + `status=completed` | 50 ms |

---

## 4. Bottlenecks & Failure Modes

Where the system slows down, fails, or loses data, in rough order of likelihood
in production:

| # | Bottleneck | Where it shows up | Why it's the limit | Mitigation today | Mitigation at scale |
|---|---|---|---|---|---|
| 1 | **LLM classify latency** | Job wall-clock | Serial calls to Gemini at ~1–3s each; 100 uncategorised rows = 5 batches × 1–3s = 5–15s | Batching (20 per call) | Parallelise batches; pre-classify during upload via streaming; cache common merchant→category mappings |
| 2 | **Gemini API rate limits** | HTTP 429s from LLM | Free tier quotas (per-minute + per-day) | Tenacity retries with backoff | Multiple LLM providers with circuit breaker; queue priority for retries; reserve quota for retries |
| 3 | **Worker single-process ETL** | Long CSVs | `pd.read_csv` is in-process, blocking; 100k rows ≈ 5–10s of CPU | None today | Move ETL to a dedicated worker pool; stream-parse CSV instead of full `read_csv` |
| 4 | **Postgres `INSERT … bulk`** | Large CSVs | Inserting 100k transactions is one transaction | SQLAlchemy `add_all` (one transaction) | COPY protocol via `COPY … FROM STDIN`; chunked commits every 5k rows |
| 5 | **Redis as SPOF for queue** | Worker pickup stalls | If Redis dies, no new jobs are picked up (existing jobs keep processing) | Compose restarts Redis | Redis Sentinel / Cluster; or replace with Postgres-backed queue (LISTEN/NOTIFY, or `SKIP LOCKED`) |
| 6 | **Upload streaming blocks the request thread** | Upload latency | `routes/jobs.py:90` reads the whole CSV *during the request* to count rows | None today — fast on 10 MiB, painful on 100 MiB | Move counting to the worker; use `wc -l` subprocess for cheap row count; or skip raw count |
| 7 | **N+1 status queries** | Polling load | 1000 clients polling `/jobs/{id}/status` per second = 1000 reads/sec | None today | Add `Cache-Control: max-age=2` for short-window polling; or push status via WebSocket / SSE |
| 8 | **No backpressure on uploads** | Disk full | Upload dir is a shared volume; no quota per tenant | Max upload size (10 MiB) | Per-tenant quota; S3-style presigned uploads to bypass the API entirely |
| 9 | **`completed_at` timezone-naive** | Cross-region deploys | Stored as naive datetime; "Z" suffix added on serialise | None today | `DateTime(timezone=True)` + `TIMESTAMPTZ` in Postgres |
| 10 | **LLM `temperature=0.7` for summary** | Reproducibility | Different runs produce different narratives | Deterministic payload shape | Pin temperature for audit/repro runs; store both deterministic + narrative |

### Failure modes already handled (PDF §5(e))

- LLM classify failure for a batch → rows marked `llm_failed=True`, job completes ✅
- LLM summary failure → deterministic fallback narrative + rule-based risk_level ✅
- ETL error (bad CSV, IO error) → job marked `failed`, error_message stored ✅
- Worker crash mid-job → RQ re-enqueues after visibility timeout; idempotent
  re-run because `attach_transactions` is a fresh insert per job ✅
- Redis restart → queue rebuilds from in-flight RQ jobs (workers may
  re-process; idempotency covers this) ✅

### Failure modes not yet handled (would need work)

- Postgres outage mid-write → worker raises; job stays `processing` until RQ
  retries → eventually `failed`. No replay tool today.
- Network partition between worker and LLM → retries exhaust → job completes
  with `llm_failed` rows. Acceptable; no data loss.
- Malicious 10 MiB upload every second → disk fills in minutes. Needs rate
  limiting (see [ROADMAP.md](ROADMAP.md)).

---

## 5. Scaling Strategy

How to grow from "1 VM, demo workload" to "10k+ jobs/day":

![Scaling strategy: four phases from single-host to event-sourced](images/scalling.png)

*Scaling strategy overview: Phase 1 (single host, today) → Phase 2 (horizontal workers, ~1k jobs/day) → Phase 3 (multi-host prod-grade, ~100k jobs/day) → Phase 4 (event-sourced, 1M+ jobs/day).*

### Phase 1 — Single-host, vertical scale (today)

One API container + one worker container + Postgres + Redis on one host.
Comfortable up to ~100 concurrent jobs/day.

### Phase 2 — Horizontal worker scale (10× growth, ~1k jobs/day)

- Scale workers: `docker compose up --scale worker=N` (RQ supports it
  out-of-the-box).
- Add `WORKER_CONCURRENCY` (already in `Settings`, currently unused) →
  `rq worker --workers N` to run N tasks per container.
- Add a Redis-backed **RQ scheduler** for periodic cleanup of stale jobs.
- Add a worker pool **dedicated to LLM calls** (network-bound) separate from
  ETL workers (CPU-bound). Different container images, different
  resource limits.

```
                    ┌─── ETL workers (CPU-bound, fast)
worker-pool-A ──────┤
                    └─── LLM workers (network-bound, slow)
worker-pool-B
```

### Phase 3 — Multi-host, prod-grade (100× growth, ~100k jobs/day)

| Concern | Solution |
|---|---|
| API horizontal scale | Run N API replicas behind a load balancer; sessions are stateless → trivial |
| Worker horizontal scale | N worker containers, possibly on a separate node pool |
| Redis HA | Redis Sentinel (3-node) or AWS ElastiCache with cluster mode |
| Postgres HA | Managed Postgres (RDS / Cloud SQL) with read replicas for the `/results` endpoint |
| Object storage for uploads | Replace `upload_dir` volume with S3; the API gets a presigned URL, the worker downloads from S3 |
| Long CSV processing | Stream CSV in ETL (don't `read_csv` the whole thing); ETL becomes O(1) memory |
| Backpressure on uploads | Per-tenant rate limit (token bucket); 429 if exceeded |
| Large bulk inserts | `COPY` protocol, chunked into 5k-row batches, every batch a transaction |
| Status read fan-out | Add a read replica + caching layer (Redis) for `/jobs/{id}/status` — most polls hit the cache |
| LLM cost / latency | Cache `(merchant, currency_band, amount_band) → category` in Redis with TTL; pre-warm for top merchants |

### Phase 4 — Beyond 1M jobs/day (architecture shift)

At this scale, the synchronous `Job` row → single worker → single LLM
pattern needs to break:

- **Event-sourced pipeline**: `Job created` → `Job cleaned` → `Job classified`
  → `Job summarised` are separate Kafka topics. Each stage is its own
  autoscaled consumer group. Failures replay from the topic, not from a
  retry queue.
- **Streaming ETL**: replace `pd.read_csv` with a streaming parser
  (`pyarrow.csv` or hand-rolled) so memory is constant in CSV size.
- **Outbox pattern** for DB writes: the worker writes "events" to an `outbox`
  table in the same transaction as the domain change; a separate process
  publishes them to Kafka. Solves the dual-write problem.
- **Per-tenant LLM routing**: enterprise tenants get a dedicated Gemini
  project with higher quotas; free tier gets the shared quota.
- **AsyncAPI spec** for the internal event contracts.

---

## 6. Capacity Estimates

Back-of-envelope numbers for sizing decisions. Assumptions:
CSV ≈ 1,000 rows, 20% uncategorised, 5 LLM batches, 5s/job.

| Workload | Jobs/day | API QPS (peak) | Worker count | Postgres size / month | Notes |
|---|---|---|---|---|---|
| **Demo / interview** | ~10 | <1 | 1 | <1 MB | Single VM, in-memory-friendly |
| **Small team** | 100 | ~5 | 2 | ~30 MB | One VM, Postgres + Redis |
| **Mid-market** | 10,000 | ~50 | 10–20 | ~3 GB | Multi-VM, Redis Sentinel, read replica |
| **Enterprise** | 1,000,000 | ~500 | 200+ | ~300 GB | Kafka, S3, multi-region |

### Storage math

- 1 transaction row ≈ 200 bytes (with all fields + LLM response)
- 1 summary row ≈ 2 KB
- 1M jobs/month × 1,000 txns/job ≈ 200 GB/month — needs partitioning by
  `created_at` and a 90-day retention policy at this scale.

### Cost math (very rough, AWS)

| Tier | Monthly cost | What you get |
|---|---|---|
| Demo | ~$5 | 1× t3.small API + 1× t3.small worker + RDS db.t3.micro + ElastiCache cache.t3.micro |
| Small team | ~$80 | 2× t3.medium API + 2× t3.medium worker + RDS db.t3.medium + ElastiCache cache.t3.medium |
| Mid-market | ~$2,000 | 4× m6i.large API + 10× m6i.large worker + RDS db.m6i.large + ElastiCache + ALB |
| Enterprise | ~$30k+ | Multi-AZ, multi-region, Kafka, S3, dedicated LLM quota |

---

## 7. Scaling Knobs Already in Codebase

| Knob | Where | Default |
|---|---|---|
| `LLM_BATCH_SIZE` | `config.py:35` | 20 rows |
| `MAX_UPLOAD_BYTES` | `config.py:39` | 10 MiB |
| `WORKER_CONCURRENCY` | `config.py:30` | 1 (unused — wired in Phase 2) |
| `RQ_QUEUE_NAME` | `config.py:29` | `default` |
| Postgres pool size | `database.py:24` | SQLAlchemy default (5 + overflow) |
| `pool_pre_ping` | `database.py:27` | `True` (reconnects on stale conns) |