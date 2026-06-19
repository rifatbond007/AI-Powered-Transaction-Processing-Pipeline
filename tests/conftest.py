"""Shared pytest fixtures for the test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Ensure tests use a fresh, isolated environment — no real Postgres/Redis.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("USE_IN_MEMORY_STORE", "1")
os.environ.setdefault("UPLOAD_DIR", "/tmp/tx-test-uploads")
os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-real")

# Minimal sample CSV covering the PDF §5 cleaning rules.
#   - Mixed date formats (dd-mm-yyyy, yyyy/mm/dd, yyyy-mm-dd)
#   - Currency symbol prefix ($)
#   - Lowercase currency (inr) and status (success)
#   - Missing txn_id (regenerated)
#   - Duplicate (TXN1000 second occurrence) -> quarantined
#   - Missing account_id -> quarantined
#   - Unparseable date -> quarantined
#   - Empty category -> "Uncategorised"
SAMPLE_CSV = """txn_id,date,merchant,amount,currency,status,category,account_id
TXN1000,23-11-2024,Amazon,423.91,INR,FAILED,Shopping,ACC004
TXN1001,2024/02/05,Swiggy,$11325.79,USD,success,Food,ACC004
TXN1002,2024-07-15,Flipkart,146100.68,INR,SUCCESS,Shopping,ACC005
TXN1003,17-02-2024,Zomato,2536.35,USD,SUCCESS,Food,ACC001
,04-09-2024,Flipkart,10882.55,inr,SUCCESS,Shopping,ACC003
TXN1001,2024/02/05,Swiggy,$11325.79,USD,success,Food,ACC004
,25-06-2024,Jio Recharge,4004.59,INR,PENDING,,ACC003
,bad-date,Jio Recharge,100.00,INR,SUCCESS,Utilities,ACC003
,20-11-2024,Ola,12448.75,inr,FAILED,Transport,
TXN1000,23-11-2024,Amazon,423.91,INR,FAILED,Shopping,ACC004
"""


@pytest.fixture()
def sample_csv_path(tmp_path: Path) -> Path:
    """Write the sample CSV to a temp file and return its path."""
    p = tmp_path / "sample.csv"
    p.write_text(SAMPLE_CSV)
    return p


@pytest.fixture()
def real_csv_path() -> Path:
    """Path to the real transactions.csv shipped with the assignment."""
    return Path(__file__).resolve().parent.parent / "transactions.csv"


@pytest.fixture()
def in_memory_store():
    """A fresh in-memory JobStore for tests that need the store directly."""
    from app.storage import InMemoryJobStore

    return InMemoryJobStore()
