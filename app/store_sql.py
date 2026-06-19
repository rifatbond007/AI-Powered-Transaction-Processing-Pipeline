"""SQLAlchemy-backed :class:`TransactionStore` implementation.

Implements the same interface as :class:`app.store.InMemoryTransactionStore`
so the route layer is storage-agnostic. All filtering, pagination, and
sorting are done in the database.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import and_, func, select, true
from sqlalchemy.orm import Session, sessionmaker

from app.database import serialize_row
from app.models import Transaction
from app.store import TransactionStore


class SqlTransactionStore(TransactionStore):
    """Postgres (or SQLite) implementation of the storage interface."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _with_session(self) -> Session:
        return self._session_factory()

    # ---- Write API --------------------------------------------------------

    def insert_many(self, rows: list[dict[str, Any]]) -> None:
        """Bulk insert using a single transaction (fast for large loads)."""
        with self._with_session() as session, session.begin():
            for r in rows:
                session.add(Transaction(**self._coerce(r)))

    @staticmethod
    def _coerce(row: dict[str, Any]) -> dict[str, Any]:
        """Convert ETL dict shape -> ORM kwargs (parse ISO dates, etc.)."""
        out = dict(row)
        if isinstance(out.get("date"), str):
            out["date"] = date.fromisoformat(out["date"])
        out.setdefault("merchant", "")
        out.setdefault("category", "")
        out.setdefault("notes", "")
        out.setdefault("status", "")
        out.setdefault("is_suspicious", False)
        return out

    # ---- Read API ---------------------------------------------------------

    def _build_filter_clauses(
        self,
        start_date: date | None,
        end_date: date | None,
        status: str | None,
        category: str | None,
        account_id: str | None,
        currency: str | None,
    ) -> list[Any]:
        clauses = []
        if start_date is not None:
            clauses.append(Transaction.date >= start_date)
        if end_date is not None:
            clauses.append(Transaction.date <= end_date)
        if status is not None:
            clauses.append(Transaction.status == status)
        if category is not None:
            clauses.append(Transaction.category == category)
        if account_id is not None:
            clauses.append(Transaction.account_id == account_id)
        if currency is not None:
            clauses.append(Transaction.currency_original == currency)
        return clauses

    def get_by_id(self, txn_id: str) -> dict[str, Any] | None:
        with self._with_session() as session:
            row = session.get(Transaction, txn_id)
            return serialize_row(row) if row else None

    def query(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        status: str | None = None,
        category: str | None = None,
        account_id: str | None = None,
        currency: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = self._build_filter_clauses(
            start_date, end_date, status, category, account_id, currency
        )
        with self._with_session() as session:
            stmt = select(Transaction).where(and_(true(), *clauses))
            stmt = stmt.order_by(Transaction.date.desc(), Transaction.txn_id.asc())
            stmt = stmt.limit(limit).offset(offset)
            rows = session.execute(stmt).scalars().all()
            return [serialize_row(r) for r in rows]

    def count(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        status: str | None = None,
        category: str | None = None,
        account_id: str | None = None,
        currency: str | None = None,
    ) -> int:
        clauses = self._build_filter_clauses(
            start_date, end_date, status, category, account_id, currency
        )
        with self._with_session() as session:
            stmt = select(func.count(Transaction.txn_id)).where(and_(true(), *clauses))
            return int(session.execute(stmt).scalar_one())

    def get_suspicious(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self._with_session() as session:
            stmt = (
                select(Transaction)
                .where(Transaction.is_suspicious.is_(True))
                .order_by(Transaction.date.desc(), Transaction.txn_id.asc())
                .limit(limit)
                .offset(offset)
            )
            return [serialize_row(r) for r in session.execute(stmt).scalars().all()]

    def count_suspicious(self) -> int:
        with self._with_session() as session:
            stmt = select(func.count(Transaction.txn_id)).where(Transaction.is_suspicious.is_(True))
            return int(session.execute(stmt).scalar_one())

    def compute_summary(self) -> dict[str, Any]:
        with self._with_session() as session:
            total = session.execute(select(func.count(Transaction.txn_id))).scalar_one() or 0
            amount_sum = (
                session.execute(
                    select(func.coalesce(func.sum(Transaction.amount_inr), 0.0))
                ).scalar_one()
                or 0.0
            )
            by_status = dict(
                session.execute(
                    select(Transaction.status, func.count(Transaction.txn_id)).group_by(
                        Transaction.status
                    )
                ).all()
            )
            by_category = dict(
                session.execute(
                    select(Transaction.category, func.count(Transaction.txn_id)).group_by(
                        Transaction.category
                    )
                ).all()
            )
            by_currency = dict(
                session.execute(
                    select(Transaction.currency_original, func.count(Transaction.txn_id)).group_by(
                        Transaction.currency_original
                    )
                ).all()
            )
            return {
                "total_transactions": int(total),
                "total_amount_inr": round(float(amount_sum), 2),
                "by_status": {k or "UNKNOWN": int(v) for k, v in by_status.items()},
                "by_category": {k or "UNKNOWN": int(v) for k, v in by_category.items()},
                "by_currency_original": {k or "UNKNOWN": int(v) for k, v in by_currency.items()},
            }
