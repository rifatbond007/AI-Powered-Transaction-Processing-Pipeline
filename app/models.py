"""SQLAlchemy ORM models for the job-based transactions service."""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base — every ORM model inherits from this."""


# Constants — keep in lockstep with app/worker.py status writes.
JOB_STATUSES = ("pending", "processing", "completed", "failed")
RISK_LEVELS = ("low", "medium", "high")


class Job(Base):
    """A single CSV upload + its processing outcome (PDF §6)."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    row_count_raw: Mapped[int] = mapped_column(Integer, nullable=False)
    row_count_clean: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Job {self.id} {self.status}>"


class Transaction(Base):
    """A single cleaned + classified transaction row, scoped to a job (PDF §6)."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    txn_id: Mapped[str] = mapped_column(String(64), nullable=False)
    date: Mapped[_date] = mapped_column(Date, nullable=False)
    merchant: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="Uncategorised")
    account_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    is_anomaly: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    anomaly_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("job_id", "txn_id", name="uq_transactions_job_txn"),
        Index("ix_transactions_job_date", "job_id", "date"),
        Index("ix_transactions_job_anomaly", "job_id", "is_anomaly"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Transaction {self.txn_id} {self.amount}{self.currency}>"


class JobSummary(Base):
    """LLM-generated summary + computed totals for a job (PDF §6)."""

    __tablename__ = "job_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    total_spend_inr: Mapped[float] = mapped_column(Float, nullable=False)
    total_spend_usd: Mapped[float] = mapped_column(Float, nullable=False)
    top_merchants: Mapped[list] = mapped_column(JSON, nullable=False)
    anomaly_count: Mapped[int] = mapped_column(Integer, nullable=False)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<JobSummary {self.job_id} {self.risk_level}>"
