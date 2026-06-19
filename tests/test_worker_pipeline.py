"""End-to-end worker pipeline tests with mocked LLM (PDF §5)."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("USE_IN_MEMORY_STORE", "1")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")


def _fake_classify(batch):
    return {"categories": ["Food"] * len(batch), "raw": "stub"}


def _fake_summarize(_payload):
    return {
        "narrative": "All transactions look routine.",
        "risk_level": "low",
        "top_3_merchants": [{"merchant": "Swiggy", "total_inr": 1000.0}],
        "raw": "stub",
    }


def test_worker_processes_csv_end_to_end(sample_csv_path) -> None:
    """Worker reads CSV -> cleans -> flags anomalies -> classifies -> summarises -> persists."""
    from app.services import llm, worker
    from app.dependencies import get_job_store
    from app.adapters.storage import InMemoryJobStore

    store = InMemoryJobStore()
    get_job_store.__globals__["_store"] = store  # type: ignore[attr-defined]
    # Easier: just set the module-level reference directly.
    import app.dependencies as deps

    deps._store = store

    job = store.create_job(filename="sample.csv", row_count_raw=0)
    store.set_row_count_raw(job.id, 10)

    with (
        patch.object(llm, "_classify_call", side_effect=_fake_classify),
        patch.object(llm, "_summarize_call", side_effect=_fake_summarize),
    ):
        out = worker.process_job(job.id, str(sample_csv_path))

    assert out["rows"] == 6
    assert out["quarantined"] == 4
    # Swiggy row in USD -> usd_domestic anomaly
    assert out["anomalies"] >= 1
    job_row = store.get_job(job.id)
    assert job_row is not None
    assert job_row.status == "completed"
    assert job_row.row_count_clean == 6
    summary = store.get_summary(job.id)
    assert summary is not None
    assert summary.narrative == "All transactions look routine."
    assert summary.risk_level == "low"


def test_worker_marks_job_failed_on_etl_error(tmp_path) -> None:
    import app.dependencies as deps
    from app.services import worker
    from app.adapters.storage import InMemoryJobStore

    store = InMemoryJobStore()
    deps._store = store

    job = store.create_job(filename="missing.csv", row_count_raw=0)
    store.set_row_count_raw(job.id, 0)

    with pytest.raises((FileNotFoundError, OSError, ValueError)):
        worker.process_job(job.id, str(tmp_path / "does_not_exist.csv"))

    job_row = store.get_job(job.id)
    assert job_row is not None
    assert job_row.status == "failed"
    assert job_row.error_message  # non-empty


def test_worker_marks_llm_failure_does_not_fail_job(sample_csv_path) -> None:
    """PDF §5(e): a failed LLM call marks the batch llm_failed, not the whole job."""
    import app.dependencies as deps
    from app.services import llm, worker
    from app.adapters.storage import InMemoryJobStore

    store = InMemoryJobStore()
    deps._store = store

    job = store.create_job(filename="sample.csv", row_count_raw=0)
    store.set_row_count_raw(job.id, 10)

    with (
        patch.object(llm, "_classify_call", return_value={"llm_failed": True, "error": "boom"}),
        patch.object(
            llm,
            "_summarize_call",
            return_value={
                "narrative": "stub",
                "risk_level": "low",
                "top_3_merchants": [],
                "raw": "stub",
            },
        ),
    ):
        out = worker.process_job(job.id, str(sample_csv_path))

    job_row = store.get_job(job.id)
    assert job_row is not None
    assert job_row.status == "completed"
    # At least the Jio Recharge row was Uncategorised and the batch failed.
    assert out["llm_failures"] >= 1


def test_upload_cleanup_runs_on_success(sample_csv_path, tmp_path, monkeypatch) -> None:
    """After the worker finishes, the temp upload file is removed."""
    import app.dependencies as deps
    from app.services import llm, worker
    from app.adapters.storage import InMemoryJobStore

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    store = InMemoryJobStore()
    deps._store = store

    job = store.create_job(filename="test.csv", row_count_raw=0)
    store.set_row_count_raw(job.id, 10)

    # Use the same upload_dir the worker reads from settings.
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))

    csv_path = upload_dir / f"{job.id}.csv"
    csv_path.write_text(sample_csv_path.read_text())

    with (
        patch.object(llm, "_classify_call", side_effect=_fake_classify),
        patch.object(llm, "_summarize_call", side_effect=_fake_summarize),
    ):
        worker.process_job(job.id, str(csv_path))

    assert not csv_path.exists()
