# Roadmap

Production improvements to add given a longer runway, ranked by impact and
grouped by horizon.

For the broader architectural and scaling context, see
[ARCHITECTURE.md](ARCHITECTURE.md). For metrics, logs, traces, and alerts,
see [OBSERVABILITY.md](OBSERVABILITY.md).

---

## Short-term (1–2 weeks)

1. **`InMemoryJobStore` implementation** of the `JobStore` ABC for unit tests
   that don't want SQLite, and a true "no DB at all" `make dev` mode.
2. **Alembic migrations** with a baseline, so schema evolution is reviewable.
3. **Idempotent upload endpoint** — dedupe by file hash so re-uploading the
   same CSV returns the existing `job_id` instead of reprocessing.
4. **Quarantine exposure** in `/jobs/{id}/results` — currently only the count
   is logged. Returning the bad rows with their reasons would help users
   debug their data.
5. **Per-job cancellation** — `DELETE /jobs/{id}` that flips status to
   `cancelled` and tells the worker to bail (cooperative cancel via a
   `cancelled_at` column).
6. **Structured JSON logs** (`structlog`) + request-id correlation across
   API → worker → DB.

## Medium-term (1–2 months)

7. **Prometheus `/metrics`** — job counts by status, LLM latency histogram,
   worker queue depth. (See [OBSERVABILITY.md](OBSERVABILITY.md) for the
   full metric catalogue.)
8. **Rate limiting** on `/jobs/upload` (token bucket per IP) and per-tenant
   `MAX_UPLOAD_BYTES`.
9. **OpenAPI examples** for every endpoint — improves DX of `/docs`.
10. **Pytest coverage threshold raised to 85–90%** with the missing
    edge-case tests added (size limit, wrong content type, empty file,
    queue-down behavior).

## Long-term (quarter+)

11. **Pre-commit hooks** — `ruff format`, `ruff check`, `pytest -x` on
    staged files.
12. **Anomaly rule configurability** — accept a YAML/JSON of domestic brands
    and the median multiplier per environment.