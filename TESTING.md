# Testing Guide

## Prerequisites

```bash
make up
```

Wait for all containers to be healthy (~10s):

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

---

## 1. Upload CSV

```bash
JOB_ID=$(curl -s -F "file=@transactions.csv;type=text/csv" \
  http://localhost:8000/jobs/upload | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "job_id: $JOB_ID"
```

## 2. Poll Status (includes summary when completed)

```bash
curl http://localhost:8000/jobs/$JOB_ID/status | python3 -m json.tool
```

To poll until done:

```bash
for i in 1 2 3 4 5 6 7 8; do
  sleep 2
  STATUS=$(curl -s http://localhost:8000/jobs/$JOB_ID/status)
  S=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "attempt $i: $S"
  if [ "$S" = "completed" ] || [ "$S" = "failed" ]; then
    echo "$STATUS" | python3 -m json.tool
    break
  fi
done
```

## 3. Get Full Results

```bash
curl http://localhost:8000/jobs/$JOB_ID/results | python3 -m json.tool
```

Quick summary:

```bash
curl -s http://localhost:8000/jobs/$JOB_ID/results | python3 -c "
import sys, json
d = json.load(sys.stdin)
s = d['summary']
print(f'Transactions:  {len(d[\"transactions\"])}')
print(f'LLM failures:  {d[\"llm_failures\"]}')
print(f'Total (INR):   {s[\"total_spend_inr\"]}')
print(f'Total (USD):   {s[\"total_spend_usd\"]}')
print(f'Anomalies:     {s[\"anomaly_count\"]}')
print(f'Risk level:    {s[\"risk_level\"]}')
print(f'Narrative:     {s[\"narrative\"][:100]}...')
"
```

## 4. List All Jobs

```bash
curl "http://localhost:8000/jobs?limit=5" | python3 -m json.tool
```

## 5. Filter Jobs by Status

```bash
curl "http://localhost:8000/jobs?status=completed"
curl "http://localhost:8000/jobs?status=pending"
curl "http://localhost:8000/jobs?status=failed"
```

## 6. Error Responses

```bash
# 404 - unknown job status
curl -w "\nHTTP %{http_code}\n" http://localhost:8000/jobs/nonexistent/status

# 404 - unknown job results
curl -w "\nHTTP %{http_code}\n" http://localhost:8000/jobs/nonexistent/results

# 409 - results before job completes (replace with a pending job_id)
curl -w "\nHTTP %{http_code}\n" http://localhost:8000/jobs/<pending_job_id>/results

# 413 - file too large
dd if=/dev/zero bs=1M count=11 | curl -s -F "file=@-;type=text/csv" \
  http://localhost:8000/jobs/upload

# 415 - wrong content type
curl -s -F "file=@transactions.csv;type=application/pdf" \
  http://localhost:8000/jobs/upload
```

## 7. One-Liner: Full Pipeline

```bash
JOB_ID=$(curl -s -F "file=@transactions.csv;type=text/csv" \
  http://localhost:8000/jobs/upload | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['job_id'])") && \
echo "job_id: $JOB_ID" && \
sleep 12 && \
echo "=== STATUS ===" && \
curl -s http://localhost:8000/jobs/$JOB_ID/status | python3 -m json.tool && \
echo "=== RESULTS ===" && \
curl -s http://localhost:8000/jobs/$JOB_ID/results | python3 -c "
import sys, json
d = json.load(sys.stdin)
s = d['summary']
print(f'  txns={len(d[\"transactions\"])}  anomalies={s[\"anomaly_count\"]}  risk={s[\"risk_level\"]}')
print(f'  narrative: {s[\"narrative\"][:120]}')"
```

## 8. Docker Logs

```bash
# API logs
docker logs tx-api --tail 20

# Worker logs
docker logs tx-worker --tail 20

# Postgres query
docker exec -it tx-postgres psql -U postgres -d transactions -c \
  "SELECT status, count(*) FROM jobs GROUP BY status;"
```
