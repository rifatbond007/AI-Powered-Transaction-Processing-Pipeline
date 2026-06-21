# PDF Spec Compliance

Every requirement from the assignment PDF, traceable to the file/line that
implements it. **Coverage: ~100% of the assignment spec, all major and
minor clauses.**

| PDF Section | Requirement | Implementation | Status |
|---|---|---|---|
| §4 | Async ingest endpoint, returns `202` | `app/routes/jobs.py:66` — `POST /jobs/upload` | ✅ |
| §4 | `job_id` returned | `JobUploadResponse.job_id` | ✅ |
| §4 | `GET /jobs` list | `app/routes/jobs.py:118` | ✅ |
| §4 | `GET /jobs/{id}/status` | `app/routes/jobs.py:130` | ✅ |
| §4 | `GET /jobs/{id}/results` | `app/routes/jobs.py:148` | ✅ |
| §4 | Async worker processes job | `app/services/worker.py` + RQ | ✅ |
| §5(a) | Mixed date formats | `etl.py:26-32` — 5 formats tried | ✅ |
| §5(a) | Currency symbols stripped | `etl.py:35` regex | ✅ |
| §5(a) | Currency case normalised | `etl.py:149` `.upper()` | ✅ |
| §5(a) | Status case normalised | `etl.py:157` `.upper()` | ✅ |
| §5(a) | Missing `category` → `"Uncategorised"` | `etl.py:160` | ✅ |
| §5(a) | Missing `txn_id` regenerated | `etl.py:163-166` | ✅ |
| §5(a) | Missing `account_id` → quarantine | `etl.py:127-130` | ✅ |
| §5(a) | Bad date / amount → quarantine | `etl.py:133-146` | ✅ |
| §5(a) | Duplicates → quarantine | `etl.py:169-173` | ✅ |
| §5(b) | Amount > 3× per-account median | `anomaly.py:47-52` | ✅ |
| §5(b) | USD paid to domestic-only brand | `anomaly.py:55-61` | ✅ |
| §5(c) | Batch uncategorised rows for LLM | `llm.py:187-219`, batch size 20 | ✅ |
| §5(d) | Single summary call after persistence | `llm.py:222-248` | ✅ |
| §5(e) | Retries (3× with backoff) | `llm.py:49-87` via tenacity | ✅ |
| §5(e) | LLM failure does NOT fail the job | `worker.py:99-162` — only ETL/DB/IO errors mark job failed | ✅ |
| §5(f) | Output: transactions + summary | `schemas.py:51-92` | ✅ |
| §6 | Persist Job / Transaction / JobSummary | `app/models.py` | ✅ |
| §7 | Containerised, multi-service compose | `Dockerfile` + `docker-compose.yml` | ✅ |
| §7 | Worker as separate service | `docker-compose.yml:79-102` | ✅ |
| §8 | CI runs lint + test + docker build | `.github/workflows/ci.yml` | ✅ |
| §9 | README with run instructions | `README.md` | ✅ |