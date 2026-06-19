"""Tests for the SQLAlchemy store and Redis cache layer.

These tests use:
  - **SQLite in-memory** for the database (fast, no service needed).
  - **fakeredis** for the cache (in-process, no Redis server needed).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import cache as cache_module
from app.models import Base, Transaction
from app.store_sql import SqlTransactionStore

# ----- Fixtures -------------------------------------------------------------


@pytest.fixture()
def sqlite_session_factory():
    """Build an in-memory SQLite engine, create tables, return a factory."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@pytest.fixture()
def sql_store(sqlite_session_factory) -> SqlTransactionStore:
    return SqlTransactionStore(sqlite_session_factory)


@pytest.fixture()
def fake_redis(monkeypatch):
    """Swap the redis client for a fakeredis instance."""
    import fakeredis

    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(cache_module, "_client", client)
    return client


# ----- SqlTransactionStore tests -------------------------------------------


def test_insert_many_and_count(sql_store: SqlTransactionStore) -> None:
    sql_store.insert_many(
        [
            {
                "txn_id": "T1",
                "date": "2024-01-15",
                "merchant": "Amazon",
                "amount_original": 100.0,
                "currency_original": "INR",
                "amount_inr": 100.0,
                "status": "SUCCESS",
                "category": "Shopping",
                "account_id": "ACC001",
                "notes": "",
                "is_suspicious": False,
            },
            {
                "txn_id": "T2",
                "date": "2024-02-20",
                "merchant": "Swiggy",
                "amount_original": 50.0,
                "currency_original": "USD",
                "amount_inr": 50.0 * 83.2,
                "status": "FAILED",
                "category": "Food",
                "account_id": "ACC002",
                "notes": "",
                "is_suspicious": False,
            },
        ]
    )
    assert sql_store.count() == 2


def test_get_by_id_found_and_missing(sql_store: SqlTransactionStore) -> None:
    sql_store.insert_many(
        [
            {
                "txn_id": "T1",
                "date": "2024-01-15",
                "merchant": "X",
                "amount_original": 1.0,
                "currency_original": "INR",
                "amount_inr": 1.0,
                "status": "SUCCESS",
                "category": "X",
                "account_id": "A",
                "notes": "",
                "is_suspicious": False,
            }
        ]
    )
    assert sql_store.get_by_id("T1") is not None
    assert sql_store.get_by_id("T1")["txn_id"] == "T1"
    assert sql_store.get_by_id("NOPE") is None


def test_query_with_filters(sql_store: SqlTransactionStore) -> None:
    rows = [
        {
            "txn_id": f"T{i}",
            "date": f"2024-{i:02d}-01",
            "merchant": "M",
            "amount_original": 10.0,
            "currency_original": "INR",
            "amount_inr": 10.0,
            "status": "SUCCESS" if i % 2 == 0 else "FAILED",
            "category": "CatA" if i < 3 else "CatB",
            "account_id": "ACC1",
            "notes": "",
            "is_suspicious": False,
        }
        for i in range(1, 6)
    ]
    sql_store.insert_many(rows)

    # status filter
    succ = sql_store.query(status="SUCCESS")
    assert {r["txn_id"] for r in succ} == {"T2", "T4"}
    assert sql_store.count(status="SUCCESS") == 2

    # category filter
    catb = sql_store.query(category="CatB")
    assert {r["txn_id"] for r in catb} == {"T3", "T4", "T5"}

    # date range
    feb = sql_store.query(start_date="2024-02-01", end_date="2024-02-28")
    assert [r["txn_id"] for r in feb] == ["T2"]


def test_query_pagination_and_ordering(sql_store: SqlTransactionStore) -> None:
    rows = [
        {
            "txn_id": f"T{i:02d}",
            "date": f"2024-01-{i:02d}",
            "merchant": "M",
            "amount_original": 1.0,
            "currency_original": "INR",
            "amount_inr": 1.0,
            "status": "SUCCESS",
            "category": "C",
            "account_id": "A",
            "notes": "",
            "is_suspicious": False,
        }
        for i in range(1, 11)  # 10 rows: T01..T10 on 2024-01-01..10
    ]
    sql_store.insert_many(rows)

    page1 = sql_store.query(limit=3, offset=0)
    page2 = sql_store.query(limit=3, offset=3)
    page4 = sql_store.query(limit=3, offset=9)
    # Newest-first: T10, T09, T08 ...
    assert [r["txn_id"] for r in page1] == ["T10", "T09", "T08"]
    assert [r["txn_id"] for r in page2] == ["T07", "T06", "T05"]
    assert [r["txn_id"] for r in page4] == ["T01"]


def test_suspicious_filter(sql_store: SqlTransactionStore) -> None:
    sql_store.insert_many(
        [
            {
                "txn_id": "OK",
                "date": "2024-01-01",
                "merchant": "M",
                "amount_original": 1.0,
                "currency_original": "INR",
                "amount_inr": 1.0,
                "status": "SUCCESS",
                "category": "C",
                "account_id": "A",
                "notes": "",
                "is_suspicious": False,
            },
            {
                "txn_id": "SUSP",
                "date": "2024-01-02",
                "merchant": "M",
                "amount_original": 200_000.0,
                "currency_original": "INR",
                "amount_inr": 200_000.0,
                "status": "PENDING",
                "category": "Travel",
                "account_id": "A",
                "notes": "SUSPICIOUS",
                "is_suspicious": True,
            },
        ]
    )
    assert sql_store.count_suspicious() == 1
    assert [r["txn_id"] for r in sql_store.get_suspicious()] == ["SUSP"]


def test_compute_summary(sql_store: SqlTransactionStore) -> None:
    sql_store.insert_many(
        [
            {
                "txn_id": "A",
                "date": "2024-01-01",
                "merchant": "M",
                "amount_original": 100.0,
                "currency_original": "INR",
                "amount_inr": 100.0,
                "status": "SUCCESS",
                "category": "Shopping",
                "account_id": "A1",
                "notes": "",
                "is_suspicious": False,
            },
            {
                "txn_id": "B",
                "date": "2024-01-02",
                "merchant": "M",
                "amount_original": 50.0,
                "currency_original": "USD",
                "amount_inr": 4160.0,
                "status": "FAILED",
                "category": "Food",
                "account_id": "A1",
                "notes": "",
                "is_suspicious": False,
            },
            {
                "txn_id": "C",
                "date": "2024-01-03",
                "merchant": "M",
                "amount_original": 200.0,
                "currency_original": "INR",
                "amount_inr": 200.0,
                "status": "SUCCESS",
                "category": "Shopping",
                "account_id": "A2",
                "notes": "",
                "is_suspicious": False,
            },
        ]
    )
    s = sql_store.compute_summary()
    assert s["total_transactions"] == 3
    assert s["total_amount_inr"] == 4460.0
    assert s["by_status"] == {"SUCCESS": 2, "FAILED": 1}
    assert s["by_category"] == {"Shopping": 2, "Food": 1}
    assert s["by_currency_original"] == {"INR": 2, "USD": 1}


def test_serialize_row_shape(sqlite_session_factory) -> None:
    """Verify serialize_row() produces the API dict shape via a real DB roundtrip."""
    from datetime import date as _date

    from app.database import serialize_row

    session = sqlite_session_factory()
    with session.begin():
        t = Transaction(
            txn_id="T1",
            date=_date(2024, 1, 15),
            merchant="M",
            amount_original=100.0,
            currency_original="INR",
            amount_inr=100.0,
            status="SUCCESS",
            category="C",
            account_id="A",
            notes="",
            is_suspicious=False,
        )
        session.add(t)
    # Re-fetch in a fresh session to ensure all column coercions run.
    fresh = sqlite_session_factory()
    row = fresh.get(Transaction, "T1")
    assert row is not None
    out = serialize_row(row)
    assert out["txn_id"] == "T1"
    assert out["date"] == "2024-01-15"
    assert out["merchant"] == "M"
    assert out["amount_inr"] == 100.0
    assert out["is_suspicious"] is False


# ----- Cache tests ----------------------------------------------------------


def test_cache_set_get_roundtrip(fake_redis) -> None:
    assert cache_module.cache_set_json("k", {"a": 1, "b": [1, 2]}, ttl_seconds=10)
    assert cache_module.cache_get_json("k") == {"a": 1, "b": [1, 2]}


def test_cache_get_miss_returns_none(fake_redis) -> None:
    assert cache_module.cache_get_json("nonexistent") is None


def test_cache_set_overwrites(fake_redis) -> None:
    cache_module.cache_set_json("k", {"v": 1}, ttl_seconds=10)
    cache_module.cache_set_json("k", {"v": 2}, ttl_seconds=10)
    assert cache_module.cache_get_json("k") == {"v": 2}


def test_cache_handles_corrupt_json(fake_redis) -> None:
    # Inject a non-JSON value directly.
    fake_redis.set("bad", "not json at all")
    assert cache_module.cache_get_json("bad") is None
