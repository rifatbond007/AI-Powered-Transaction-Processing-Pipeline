# Backend DevOps Assignment

A production-grade backend service that ingests, cleans, and serves a messy transactions dataset.

## Overview

This service:

- Runs a defensive **ETL pipeline** on `transactions.csv` (handles mixed date formats, currency symbols, casing, duplicates, and nulls).
- Exposes cleaned data through a **FastAPI** REST API.
- Persists data in **PostgreSQL**, with **Redis** caching for aggregates.
- Ships fully **Dockerized** with a one-command `docker compose up`.
- Has **CI** (lint + tests + docker build) on every push via GitHub Actions.

## Quick Start

```bash
# 1. Clone
git clone <your-repo-url>
cd backend-devops-assignment

# 2. Run with Docker (recommended)
cp .env.example .env
docker compose up --build

# 3. Or run locally
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
# (start postgres + redis separately, then:)
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`. Interactive docs at `/docs`.

## Endpoints

| Method | Path                       | Description                                   |
| ------ | -------------------------- | --------------------------------------------- |
| GET    | `/health`                  | Health check                                  |
| GET    | `/transactions`            | List transactions (filters + pagination)      |
| GET    | `/transactions/{txn_id}`   | Get a single transaction                      |
| GET    | `/summary`                 | Aggregate summary (Redis-cached, 60s TTL)     |
| GET    | `/suspicious`              | High-value or flagged transactions            |

See [`instruction.md`](./instruction.md) for the full rules and design decisions.

## Project Status

Built in 6 incremental segments — see the [Development Log](#development-log) below.

## License

For interview evaluation only.
