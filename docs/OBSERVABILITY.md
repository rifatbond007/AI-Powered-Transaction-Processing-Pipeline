# Observability

What to add before calling this service "production-ready".

For the broader architecture and scaling context, see
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## Table of Contents

1. [Metrics](#1-metrics)
2. [Logs](#2-logs)
3. [Traces](#3-traces)
4. [Alerts](#4-alerts)
5. [Health Checks](#5-health-checks)

---

## 1. Metrics

Prometheus exporters covering four layers:

- `jobs_created_total{tenant_id, status}` — counter
- `job_duration_seconds{stage=etl|anomaly|classify|summarise|persist}` — histogram
- `worker_queue_depth{queue=default}` — gauge (from RQ)
- `llm_call_duration_seconds{model, op=classify|summarise}` — histogram
- `llm_call_failures_total{model, reason}` — counter
- `http_requests_total{route, status}` — counter
- `http_request_duration_seconds{route}` — histogram
- `db_connection_pool_in_use` / `db_connection_pool_size` — gauges

## 2. Logs

Structured JSON via `structlog`. Every record carries a `request_id` for
correlation across services.

- **Every request:** `request_id`, `job_id`, `route`, `status`, `duration_ms`
- **Every worker task:** `job_id`, `stage`, `rows_in`, `rows_out`, `quarantined`,
  `duration_ms`, `llm_failures`
- **Every LLM call:** `job_id`, `op`, `batch_size`, `attempt`, `latency_ms`,
  `failed`

## 3. Traces

OpenTelemetry. Trace `POST /jobs/upload` → RQ task → ETL → LLM classify →
LLM summarise → DB writes, all under one `trace_id` so a slow job can be
diagnosed from the API log.

## 4. Alerts

Initial set, ordered roughly by severity:

| Alert | Condition | Severity |
|---|---|---|
| Job failure rate spike | `rate(worker_failures[5m]) > 0.1` | P3 |
| Worker queue depth growing | `rq_queue_depth > 1000 for 10m` | P2 |
| LLM failure rate | `rate(llm_call_failures[5m]) > 0.05` | P3 |
| API p99 latency | `http_request_duration_seconds:p99 > 2s for 10m` | P2 |
| Disk usage on API host | `disk_used_percent > 80` | P2 |
| Postgres connection pool exhaustion | `db_connection_pool_in_use / db_connection_pool_size > 0.9` | P1 |

## 5. Health Checks

Beyond the existing `/health` endpoint:

- **`/health/live`** — process is up (always 200 if reachable)
- **`/health/ready`** — DB reachable, Redis reachable, JobStore responds to
  `SELECT 1` (503 if any dep is down). Used by the load balancer to take
  bad pods out of rotation.