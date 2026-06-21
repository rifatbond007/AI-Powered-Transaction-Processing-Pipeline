"""Routes for the four /jobs/* endpoints (PDF §4)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.adapters import queue as queue_module
from app.adapters.storage import JobStore
from app.config import Settings, get_settings
from app.dependencies import get_job_store
from app.schemas import (
    JobListResponse,
    JobResults,
    JobStatus,
    JobStatusSummary,
    JobSummaryRead,
    JobUploadResponse,
    LimitQuery,
    OffsetQuery,
    TransactionRead,
)
from app.services.etl import run_etl
from app.services.fx import to_inr
from app.services.upload import save_upload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _serialize_job(job) -> dict:
    return {
        "job_id": job.id,
        "filename": job.filename,
        "status": job.status,
        "row_count_raw": job.row_count_raw,
        "row_count_clean": job.row_count_clean,
        "created_at": job.created_at.isoformat() + "Z",
        "completed_at": job.completed_at.isoformat() + "Z" if job.completed_at else None,
        "error_message": job.error_message,
    }


def _serialize_txn(t) -> dict:
    return {
        "id": t.id,
        "job_id": t.job_id,
        "txn_id": t.txn_id,
        "date": t.date.isoformat() if t.date else None,
        "merchant": t.merchant or "",
        "amount": t.amount,
        "currency": t.currency,
        "status": t.status or "",
        "category": t.category or "Uncategorised",
        "account_id": t.account_id or "",
        "is_anomaly": bool(t.is_anomaly),
        "anomaly_reason": t.anomaly_reason,
        "llm_category": t.llm_category,
        "llm_raw_response": t.llm_raw_response,
        "llm_failed": bool(t.llm_failed),
    }


@router.post(
    "/upload",
    response_model=JobUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_job(
    file: UploadFile = File(..., description="CSV file to process"),
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_job_store),
) -> JobUploadResponse:
    """Accept a CSV upload, create a Job, enqueue the worker, return job_id."""
    # 1. Create the job with a placeholder raw count.
    job = store.create_job(filename=file.filename or "upload.csv", row_count_raw=0)

    # 2. Stream the upload to disk; raises 415/400/413 on failure.
    csv_path = await save_upload(
        file,
        job_id=job.id,
        upload_dir=settings.upload_dir,
        max_bytes=settings.max_upload_bytes,
    )

    # 3. Count raw rows now that the file is on disk and patch the job.
    try:
        row_count_raw = run_etl(csv_path).row_count_raw
    except Exception:
        row_count_raw = 0
    if row_count_raw != job.row_count_raw:
        store.set_row_count_raw(job.id, row_count_raw)
        job.row_count_raw = row_count_raw

    # 4. Enqueue the worker task. On enqueue failure, mark the job failed
    #    so the API caller can see the failure mode.
    try:
        queue_module.enqueue_process_job(job.id, str(csv_path))
    except Exception as e:
        logger.exception("Failed to enqueue job %s", job.id)
        store.set_job_status(job.id, "failed", error_message=f"enqueue failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="failed to enqueue job; please retry",
        ) from e

    return JobUploadResponse(
        job_id=job.id,
        filename=job.filename,
        status="pending",
        row_count_raw=row_count_raw,
        created_at=job.created_at.isoformat() + "Z",
    )


@router.get("", response_model=JobListResponse)
def list_jobs(
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
    status: str | None = Query(None, description="Filter by job status"),
    store: JobStore = Depends(get_job_store),
) -> JobListResponse:
    """List all jobs, newest first. Supports ?status= filter."""
    jobs, total = store.list_jobs(limit=limit, offset=offset, status=status)
    return JobListResponse(items=[JobStatus(**_serialize_job(j)) for j in jobs], total=total)


@router.get("/{job_id}/status", response_model=JobStatus)
def get_job_status(job_id: str, store: JobStore = Depends(get_job_store)) -> JobStatus:
    """Return the current status of a job. Includes summary if completed."""
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    data = _serialize_job(job)
    if job.status == "completed":
        summary = store.get_summary(job_id)
        if summary is not None:
            data["summary"] = JobStatusSummary(
                total_spend_inr=summary.total_spend_inr,
                anomaly_count=summary.anomaly_count,
                risk_level=summary.risk_level,
            )
    return JobStatus(**data)


@router.get("/{job_id}/results", response_model=JobResults)
def get_job_results(job_id: str, store: JobStore = Depends(get_job_store)) -> JobResults:
    """Return the full structured output of a completed job."""
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    if job.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "job not ready", "status": job.status},
        )
    summary = store.get_summary(job_id)
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="job completed but summary missing",
        )
    txns = store.list_transactions(job_id, limit=200, offset=0)
    llm_failures = sum(1 for t in txns if t.llm_failed)

    # Per-category spend breakdown (effective category = llm_category if available)
    from collections import defaultdict

    category_totals: dict[str, float] = defaultdict(float)
    for t in txns:
        effective = (t.llm_category or t.category) if not t.llm_failed else t.category
        category_totals[effective] += to_inr(t.amount, t.currency)
    category_breakdown = {k: round(v, 2) for k, v in sorted(category_totals.items())}

    return JobResults(
        job=JobStatus(**_serialize_job(job)),
        transactions=[TransactionRead.model_validate(_serialize_txn(t)) for t in txns],
        summary=JobSummaryRead(
            total_spend_inr=summary.total_spend_inr,
            total_spend_usd=summary.total_spend_usd,
            top_merchants=summary.top_merchants,
            anomaly_count=summary.anomaly_count,
            narrative=summary.narrative,
            risk_level=summary.risk_level,
        ),
        category_breakdown=category_breakdown,
        llm_failures=llm_failures,
    )
