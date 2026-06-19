"""Shared pytest fixtures for the test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

# Minimal sample CSV covering all the dirty-data rules from instruction.md.
# Notes on what's exercised by this fixture:
#   - Two date formats (dd-mm-yyyy and yyyy/mm/dd and yyyy-mm-dd)
#   - Currency symbol prefix ($)
#   - Lowercase currency (inr) and status (success)
#   - Missing txn_id (gets regenerated)
#   - Duplicate row (TXN1000 second occurrence)
#   - Missing account_id (quarantined)
#   - Unparseable date
#   - SUSPICIOUS notes
#   - Amount > 100,000 INR (suspicious by threshold)
SAMPLE_CSV = """txn_id,date,merchant,amount,currency,status,category,account_id,notes
TXN1000,23-11-2024,Amazon,423.91,INR,FAILED,Shopping,ACC004,
TXN1001,2024/02/05,Swiggy,$11325.79,USD,success,Food,ACC004,Verified
TXN1002,2024-07-15,Flipkart,146100.68,INR,SUCCESS,Shopping,ACC005,
TXN1003,17-02-2024,Zomato,2536.35,USD,SUCCESS,Food,ACC001,
,04-09-2024,Flipkart,10882.55,inr,SUCCESS,Shopping,ACC003,Refund expected
TXN1001,2024/02/05,Swiggy,$11325.79,USD,success,Food,ACC004,Verified
,25-06-2024,Jio Recharge,4004.59,INR,PENDING,,ACC003,
,bad-date,Jio Recharge,100.00,INR,SUCCESS,Utilities,ACC003,
,20-11-2024,Ola,12448.75,inr,FAILED,Transport,,SUSPICIOUS
TXN1000,23-11-2024,Amazon,423.91,INR,FAILED,Shopping,ACC004,
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
