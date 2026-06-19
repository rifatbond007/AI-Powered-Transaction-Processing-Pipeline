"""Pydantic v2 schemas for the public API (PDF §4, §6)."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

# ---- /jobs/* responses --------------------------------------------------- #


class JobStatus(BaseModel):
    """Returned by GET /jobs/{id}/status."""

    job_id: str
    filename: str
    status: str  # pending|processing|completed|failed
    row_count_raw: int
    row_count_clean: int | None
    created_at: str
    completed_at: str | None
    error_message: str | None


class JobUploadResponse(BaseModel):
    """Returned by POST /jobs/upload (202)."""

    job_id: str
    filename: str
    status: str
    row_count_raw: int
    created_at: str


class JobListResponse(BaseModel):
    """Returned by GET /jobs."""

    items: list[JobStatus]
    total: int


class TransactionRead(BaseModel):
    """A single cleaned + classified transaction."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: str
    txn_id: str
    date: str
    merchant: str
    amount: float
    currency: str
    status: str
    category: str
    account_id: str
    is_anomaly: bool
    anomaly_reason: str | None
    llm_category: str | None
    llm_raw_response: str | None
    llm_failed: bool


class JobSummaryRead(BaseModel):
    """The LLM-generated summary + computed totals."""

    total_spend_inr: float
    total_spend_usd: float
    top_merchants: list[dict[str, Any]]
    anomaly_count: int
    narrative: str
    risk_level: str


class JobResults(BaseModel):
    """Returned by GET /jobs/{id}/results when status == completed."""

    job: JobStatus
    transactions: list[TransactionRead]
    summary: JobSummaryRead
    llm_failures: int


# ---- /health response ----------------------------------------------------- #


class HealthResponse(BaseModel):
    status: str = "ok"


# ---- Query param aliases -------------------------------------------------- #


LimitQuery = Annotated[int, Field(ge=1, le=200, description="Page size (1-200)")]
OffsetQuery = Annotated[int, Field(ge=0, description="Number of rows to skip")]
