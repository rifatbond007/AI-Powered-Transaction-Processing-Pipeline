"""API contract tests for the /jobs/* endpoints (PDF §4)."""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    # Reset the store between tests with a fresh SQLite-backed SqlJobStore
    from app.dependencies import set_job_store
    from tests.conftest import make_sql_store

    store, *_ = make_sql_store()
    set_job_store(store)


def _fake_classify(batch):
    return {"categories": ["Food"] * len(batch), "raw": "stub"}


def _fake_summarize(_payload):
    return {
        "narrative": "Routine.",
        "risk_level": "low",
        "top_3_merchants": [{"merchant": "Swiggy", "total_inr": 1.0}],
        "raw": "stub",
    }


SAMPLE_CSV = (
    "txn_id,date,merchant,amount,currency,status,category,account_id\n"
    "TXN1,2024-01-15,Swiggy,100.00,INR,SUCCESS,Food,ACC1\n"
    "TXN2,2024-01-16,Ola,200.00,USD,SUCCESS,Transport,ACC1\n"
    "TXN3,2024-01-17,IRCTC,500.00,USD,SUCCESS,Travel,ACC1\n"
)


def test_upload_returns_202_and_runs_worker_inline() -> None:
    """Upload -> 202 -> we run the worker inline so status reaches completed."""
    from app.adapters import queue as queue_module
    from app.services import llm
    from app.services.worker import process_job

    # Patch enqueue to run the worker synchronously inside the test.
    def sync_enqueue(job_id, csv_path):
        with (
            patch.object(llm, "_classify_call", side_effect=_fake_classify),
            patch.object(llm, "_summarize_call", side_effect=_fake_summarize),
        ):
            return process_job(job_id, csv_path)

    with (
        patch.object(queue_module, "enqueue_process_job", side_effect=sync_enqueue),
        TestClient(__import__("app.main", fromlist=["app"]).app) as client,
    ):
        r = client.post(
            "/jobs/upload",
            files={"file": ("test.csv", io.BytesIO(SAMPLE_CSV.encode()), "text/csv")},
        )
        assert r.status_code == 202
        body = r.json()
        assert "job_id" in body
        assert body["status"] == "pending"
        job_id = body["job_id"]

        # Status should now be completed because sync_enqueue ran the worker inline.
        r = client.get(f"/jobs/{job_id}/status")
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

        # Results should be available.
        r = client.get(f"/jobs/{job_id}/results")
        assert r.status_code == 200
        data = r.json()
        assert data["summary"]["risk_level"] == "low"
        assert data["summary"]["narrative"] == "Routine."
        # Anomalies: rule B fires on the Ola and IRCTC rows (USD + domestic brands).
        assert data["summary"]["anomaly_count"] >= 2


def test_status_404_for_unknown_job() -> None:
    with TestClient(__import__("app.main", fromlist=["app"]).app) as client:
        r = client.get("/jobs/nonexistent/status")
    assert r.status_code == 404


def test_results_409_when_not_completed() -> None:
    """Manually create a job in 'pending' state and try to fetch results."""
    from app.dependencies import get_job_store

    store = get_job_store()
    job = store.create_job(filename="x.csv", row_count_raw=0)

    with TestClient(__import__("app.main", fromlist=["app"]).app) as client:
        r = client.get(f"/jobs/{job.id}/results")
    assert r.status_code == 409


def test_list_jobs_returns_created_job() -> None:
    from app.dependencies import get_job_store

    store = get_job_store()
    store.create_job(filename="a.csv", row_count_raw=0)
    store.create_job(filename="b.csv", row_count_raw=0)

    with TestClient(__import__("app.main", fromlist=["app"]).app) as client:
        r = client.get("/jobs")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


def test_list_jobs_filters_by_status() -> None:
    from app.dependencies import get_job_store

    store = get_job_store()
    j1 = store.create_job(filename="pending.csv", row_count_raw=5)
    store.set_job_status(j1.id, "pending")
    j2 = store.create_job(filename="completed.csv", row_count_raw=5)
    store.set_job_status(j2.id, "completed", row_count_clean=5)

    with TestClient(__import__("app.main", fromlist=["app"]).app) as client:
        r = client.get("/jobs?status=completed")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["job_id"] == j2.id


def test_status_includes_summary_when_completed() -> None:
    """GET /jobs/{id}/status includes high-level summary when job is completed."""
    from app.dependencies import get_job_store

    store = get_job_store()
    job = store.create_job(filename="test.csv", row_count_raw=5)
    store.set_job_status(job.id, "completed", row_count_clean=5)
    store.attach_summary(
        {
            "job_id": job.id,
            "total_spend_inr": 1000.0,
            "total_spend_usd": 12.02,
            "top_merchants": [{"merchant": "X", "total_inr": 1000.0}],
            "anomaly_count": 1,
            "narrative": "Routine.",
            "risk_level": "low",
        }
    )

    with TestClient(__import__("app.main", fromlist=["app"]).app) as client:
        r = client.get(f"/jobs/{job.id}/status")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["summary"] is not None
    assert body["summary"]["total_spend_inr"] == 1000.0
    assert body["summary"]["anomaly_count"] == 1
    assert body["summary"]["risk_level"] == "low"


def test_status_no_summary_when_pending() -> None:
    from app.dependencies import get_job_store

    store = get_job_store()
    job = store.create_job(filename="pending.csv", row_count_raw=0)

    with TestClient(__import__("app.main", fromlist=["app"]).app) as client:
        r = client.get(f"/jobs/{job.id}/status")
    assert r.status_code == 200
    assert r.json()["summary"] is None
