"""Integration tests for the FastAPI application.

Uses FastAPI's :class:`TestClient` (built on httpx) and bypasses the
``lifespan`` by using the ``app`` fixture that wires up an in-memory
store directly — so we don't depend on the real transactions.csv at test
time.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import dependencies
from app.main import create_app
from app.routes import summary as summary_routes
from app.store import InMemoryTransactionStore


@pytest.fixture()
def store() -> InMemoryTransactionStore:
    """A pre-populated in-memory store used across all API tests."""
    s = InMemoryTransactionStore()
    s.insert_many(
        [
            {
                "txn_id": "TXN_A",
                "date": "2024-01-15",
                "merchant": "Amazon",
                "amount_original": 500.0,
                "currency_original": "INR",
                "amount_inr": 500.0,
                "status": "SUCCESS",
                "category": "Shopping",
                "account_id": "ACC001",
                "notes": "",
                "is_suspicious": False,
            },
            {
                "txn_id": "TXN_B",
                "date": "2024-02-20",
                "merchant": "Swiggy",
                "amount_original": 200.0,
                "currency_original": "USD",
                "amount_inr": 200.0 * 83.2,
                "status": "FAILED",
                "category": "Food",
                "account_id": "ACC001",
                "notes": "",
                "is_suspicious": False,
            },
            {
                "txn_id": "TXN_C",
                "date": "2024-03-10",
                "merchant": "IRCTC",
                "amount_original": 150_000.0,
                "currency_original": "INR",
                "amount_inr": 150_000.0,
                "status": "PENDING",
                "category": "Travel",
                "account_id": "ACC002",
                "notes": "SUSPICIOUS",
                "is_suspicious": True,
            },
        ]
    )
    return s


@pytest.fixture()
def client(store: InMemoryTransactionStore):
    """A TestClient that uses the pre-populated store, bypassing lifespan.

    The :class:`TestClient` triggers the FastAPI ``lifespan`` on first use,
    which would otherwise re-load the real ``transactions.csv`` and replace
    our injected store. We work around this by:
      1. Pre-registering the store via :func:`dependencies.set_store`.
      2. NOT entering the TestClient as a context manager (so lifespan
         doesn't run).
    """
    dependencies.set_store(store)
    summary_routes.clear_summary_cache()
    app = create_app()
    c = TestClient(app)
    yield c
    c.close()
    dependencies.set_store(None)  # type: ignore[arg-type]


# ----- Health ---------------------------------------------------------------


def test_health_returns_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ----- /transactions list ---------------------------------------------------


def test_list_transactions_returns_all(client: TestClient) -> None:
    r = client.get("/transactions")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    # Newest-first ordering.
    assert body["items"][0]["txn_id"] == "TXN_C"


def test_list_transactions_filter_by_status(client: TestClient) -> None:
    r = client.get("/transactions", params={"status": "FAILED"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["txn_id"] == "TXN_B"


def test_list_transactions_filter_by_account(client: TestClient) -> None:
    r = client.get("/transactions", params={"account_id": "ACC002"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["account_id"] == "ACC002"


def test_list_transactions_pagination(client: TestClient) -> None:
    r = client.get("/transactions", params={"limit": 2, "offset": 0})
    body = r.json()
    assert len(body["items"]) == 2
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 0

    r2 = client.get("/transactions", params={"limit": 2, "offset": 2})
    body2 = r2.json()
    assert len(body2["items"]) == 1
    assert body2["offset"] == 2


def test_list_transactions_date_range(client: TestClient) -> None:
    r = client.get(
        "/transactions",
        params={"start_date": "2024-02-01", "end_date": "2024-02-28"},
    )
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["txn_id"] == "TXN_B"


def test_list_transactions_limit_validation(client: TestClient) -> None:
    r = client.get("/transactions", params={"limit": 9999})
    assert r.status_code == 422  # exceeds max=500


# ----- /transactions/{id} ---------------------------------------------------


def test_get_transaction_found(client: TestClient) -> None:
    r = client.get("/transactions/TXN_A")
    assert r.status_code == 200
    body = r.json()
    assert body["txn_id"] == "TXN_A"
    assert body["currency_original"] == "INR"
    assert body["amount_inr"] == 500.0


def test_get_transaction_not_found(client: TestClient) -> None:
    r = client.get("/transactions/DOES_NOT_EXIST")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


# ----- /suspicious ----------------------------------------------------------


def test_suspicious_returns_only_flagged(client: TestClient) -> None:
    r = client.get("/suspicious")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["txn_id"] == "TXN_C"
    assert body["items"][0]["is_suspicious"] is True


# ----- /summary -------------------------------------------------------------


def test_summary_aggregates_correctly(client: TestClient) -> None:
    r = client.get("/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_transactions"] == 3
    expected_total = 500.0 + 200.0 * 83.2 + 150_000.0
    assert body["total_amount_inr"] == round(expected_total, 2)
    assert body["by_status"]["SUCCESS"] == 1
    assert body["by_status"]["FAILED"] == 1
    assert body["by_status"]["PENDING"] == 1
    assert body["by_category"]["Shopping"] == 1
    assert body["by_currency_original"]["INR"] == 2
    assert body["by_currency_original"]["USD"] == 1


def test_summary_is_cached(client: TestClient) -> None:
    """Second call should hit the in-process cache."""
    # First call populates the cache.
    r1 = client.get("/summary")
    assert r1.status_code == 200

    # Mutate the store directly to verify the second call uses the cache.
    # (In-process cache returns the same object on subsequent calls.)
    s = dependencies.get_store()
    s.insert_many(
        [
            {
                "txn_id": "TXN_X",
                "date": "2024-04-01",
                "merchant": "X",
                "amount_original": 999.0,
                "currency_original": "INR",
                "amount_inr": 999.0,
                "status": "SUCCESS",
                "category": "Other",
                "account_id": "ACC999",
                "notes": "",
                "is_suspicious": False,
            }
        ]
    )
    r2 = client.get("/summary")
    assert r2.json() == r1.json()  # cached value, doesn't reflect the new row
