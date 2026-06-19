"""SQLAlchemy ORM models for the transactions service."""

from __future__ import annotations

from datetime import date as _date

from sqlalchemy import Boolean, Date, Float, Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base — every ORM model inherits from this."""


class Transaction(Base):
    """A single cleaned transaction.

    Field names mirror the API's JSON contract (see
    :class:`app.schemas.Transaction`). Indexes on the columns we filter /
    sort / join on.
    """

    __tablename__ = "transactions"

    txn_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    date: Mapped[_date] = mapped_column(Date, nullable=False, index=True)
    merchant: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    amount_original: Mapped[float] = mapped_column(Float, nullable=False)
    currency_original: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    amount_inr: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="", index=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    notes: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    is_suspicious: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    __table_args__ = (
        Index("ix_transactions_account_date", "account_id", "date"),
        Index("ix_transactions_status_date", "status", "date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Transaction {self.txn_id} {self.amount_inr}INR>"
