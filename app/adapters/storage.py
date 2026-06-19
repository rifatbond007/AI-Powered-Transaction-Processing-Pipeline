"""Storage layer for jobs, transactions, and summaries (PDF §6).

:class:`SqlJobStore` is the only implementation (Postgres via SQLAlchemy,
or SQLite in tests).

The store is the *single* source of truth for job status; the worker
updates it, the API reads from it. The RQ Redis queue is only used to
signal "a job is ready to be processed", not to store authoritative state.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import JOB_STATUSES, Job, JobSummary, Transaction


def _utcnow() -> datetime:
    """Timezone-aware UTC now (avoids the datetime.utcnow() deprecation in 3.13+)."""
    return datetime.now(UTC)


class JobStore(ABC):
    """Abstract interface for the job-storage layer."""

    @abstractmethod
    def create_job(
        self,
        *,
        filename: str,
        row_count_raw: int,
        status: str = "pending",
    ) -> Job:
        """Insert a new Job row and return it."""

    @abstractmethod
    def get_job(self, job_id: str) -> Job | None:
        """Return a Job by id, or None."""

    @abstractmethod
    def set_row_count_raw(self, job_id: str, row_count_raw: int) -> None:
        """Patch the raw row count (used after upload streaming completes)."""

    @abstractmethod
    def set_job_status(
        self,
        job_id: str,
        status: str,
        *,
        row_count_clean: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update status, optionally setting row_count_clean / error_message / completed_at."""

    @abstractmethod
    def attach_transactions(self, job_id: str, rows: list[dict[str, Any]]) -> None:
        """Bulk insert transactions for ``job_id``."""

    @abstractmethod
    def attach_summary(self, summary: dict[str, Any]) -> None:
        """Insert a JobSummary row from a dict matching the JobSummary shape."""

    @abstractmethod
    def get_summary(self, job_id: str) -> JobSummary | None:
        """Return the JobSummary for a job, or None."""

    @abstractmethod
    def list_transactions(
        self, job_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[Transaction]:
        """Return cleaned transactions for a job, newest first."""

    @abstractmethod
    def count_transactions(self, job_id: str) -> int:
        """Return the number of transactions persisted for a job."""

    @abstractmethod
    def list_jobs(
        self, *, limit: int = 50, offset: int = 0, status: str | None = None
    ) -> tuple[list[Job], int]:
        """Return (jobs, total) — newest first. Optionally filter by status."""


# --------------------------------------------------------------------------- #
# SQL implementation
# --------------------------------------------------------------------------- #


class SqlJobStore(JobStore):
    """Postgres (or SQLite) implementation of :class:`JobStore`."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _with_session(self) -> Session:
        return self._session_factory()

    def create_job(
        self,
        *,
        filename: str,
        row_count_raw: int,
        status: str = "pending",
    ) -> Job:
        if status not in JOB_STATUSES:
            raise ValueError(f"invalid status: {status}")
        job = Job(
            id=uuid.uuid4().hex,
            filename=filename,
            status=status,
            row_count_raw=row_count_raw,
            row_count_clean=None,
            created_at=_utcnow(),
            completed_at=None,
            error_message=None,
        )
        with self._with_session() as s, s.begin():
            s.add(job)
        return job

    def get_job(self, job_id: str) -> Job | None:
        with self._with_session() as s:
            return s.get(Job, job_id)

    def set_row_count_raw(self, job_id: str, row_count_raw: int) -> None:
        with self._with_session() as s, s.begin():
            job = s.get(Job, job_id)
            if job is not None:
                job.row_count_raw = row_count_raw

    def set_job_status(
        self,
        job_id: str,
        status: str,
        *,
        row_count_clean: int | None = None,
        error_message: str | None = None,
    ) -> None:
        if status not in JOB_STATUSES:
            raise ValueError(f"invalid status: {status}")
        with self._with_session() as s, s.begin():
            job = s.get(Job, job_id)
            if job is None:
                return
            job.status = status
            if row_count_clean is not None:
                job.row_count_clean = row_count_clean
            if error_message is not None:
                job.error_message = error_message
            if status in ("completed", "failed"):
                job.completed_at = _utcnow()

    def attach_transactions(self, job_id: str, rows: list[dict[str, Any]]) -> None:
        from datetime import date

        objs: list[Transaction] = []
        for r in rows:
            row = dict(r)
            if isinstance(row.get("date"), str):
                row["date"] = date.fromisoformat(row["date"])
            row.setdefault("merchant", "")
            row.setdefault("status", "")
            row.setdefault("category", "Uncategorised")
            row.setdefault("account_id", "")
            row.setdefault("is_anomaly", False)
            row.setdefault("anomaly_reason", None)
            row.setdefault("llm_category", None)
            row.setdefault("llm_raw_response", None)
            row.setdefault("llm_failed", False)
            row["job_id"] = job_id
            objs.append(Transaction(**row))
        with self._with_session() as s, s.begin():
            s.add_all(objs)

    def attach_summary(self, summary: dict[str, Any]) -> None:
        s_obj = JobSummary(
            job_id=summary["job_id"],
            total_spend_inr=summary["total_spend_inr"],
            total_spend_usd=summary["total_spend_usd"],
            top_merchants=summary["top_merchants"],
            anomaly_count=summary["anomaly_count"],
            narrative=summary["narrative"],
            risk_level=summary["risk_level"],
            created_at=_utcnow(),
        )
        with self._with_session() as s, s.begin():
            s.add(s_obj)

    def get_summary(self, job_id: str) -> JobSummary | None:
        with self._with_session() as s:
            stmt = select(JobSummary).where(JobSummary.job_id == job_id)
            return s.execute(stmt).scalar_one_or_none()

    def list_transactions(
        self, job_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[Transaction]:
        with self._with_session() as s:
            stmt = (
                select(Transaction)
                .where(Transaction.job_id == job_id)
                .order_by(desc(Transaction.date), desc(Transaction.id))
                .limit(limit)
                .offset(offset)
            )
            return list(s.execute(stmt).scalars().all())

    def count_transactions(self, job_id: str) -> int:
        with self._with_session() as s:
            stmt = select(Transaction).where(Transaction.job_id == job_id)
            return len(list(s.execute(stmt).scalars().all()))

    def list_jobs(
        self, *, limit: int = 50, offset: int = 0, status: str | None = None
    ) -> tuple[list[Job], int]:
        from sqlalchemy import func

        with self._with_session() as s:
            base = select(Job)
            total_q = select(func.count(Job.id))
            if status is not None:
                base = base.where(Job.status == status)
                total_q = total_q.where(Job.status == status)
            total = int(s.execute(total_q).scalar_one())
            stmt = base.order_by(desc(Job.created_at)).limit(limit).offset(offset)
            jobs = list(s.execute(stmt).scalars().all())
            return jobs, total

    # ---- Helpers exposed for the API layer ------------------------------- #

    def serialize_job(self, job: Job) -> dict[str, Any]:
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

    def serialize_summary(self, summary: JobSummary) -> dict[str, Any]:
        return {
            "total_spend_inr": summary.total_spend_inr,
            "total_spend_usd": summary.total_spend_usd,
            "top_merchants": summary.top_merchants,
            "anomaly_count": summary.anomaly_count,
            "narrative": summary.narrative,
            "risk_level": summary.risk_level,
        }



