"""In-memory data store for the transactions API.

This module defines a small abstract interface (:class:`TransactionStore`)
and a concrete in-memory implementation (:class:`InMemoryTransactionStore`)
backed by a list of dicts.

Segment 4 will replace this with a SQLAlchemy-backed implementation
(:class:`SqlTransactionStore`) without changing the route layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any


class TransactionStore(ABC):
    """Abstract interface every storage backend must implement."""

    @abstractmethod
    def insert_many(self, rows: list[dict[str, Any]]) -> None:
        """Bulk insert cleaned transactions."""

    @abstractmethod
    def get_by_id(self, txn_id: str) -> dict[str, Any] | None:
        """Return a single transaction by ``txn_id`` or ``None`` if not found."""

    @abstractmethod
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
        """Return a filtered, paginated list of transactions."""

    @abstractmethod
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
        """Count rows matching the same filter as :meth:`query`."""

    @abstractmethod
    def get_suspicious(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Return all transactions where ``is_suspicious`` is true."""

    @abstractmethod
    def count_suspicious(self) -> int:
        """Count suspicious transactions."""

    @abstractmethod
    def compute_summary(self) -> dict[str, Any]:
        """Compute aggregate summary statistics."""


class InMemoryTransactionStore(TransactionStore):
    """A simple in-memory :class:`TransactionStore` for development & tests.

    Thread-safety is intentionally NOT provided — FastAPI route handlers run
    on a single event loop, and a production deployment would use the
    SQLAlchemy-backed store from Segment 4.
    """

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    # ---- Write API --------------------------------------------------------

    def insert_many(self, rows: list[dict[str, Any]]) -> None:
        # Store a shallow copy so callers can't mutate our state.
        self._rows.extend(dict(r) for r in rows)

    # ---- Read API ---------------------------------------------------------

    def _matches(
        self,
        row: dict[str, Any],
        *,
        start_date: date | None,
        end_date: date | None,
        status: str | None,
        category: str | None,
        account_id: str | None,
        currency: str | None,
    ) -> bool:
        row_date = date.fromisoformat(row["date"])
        return not (
            (start_date and row_date < start_date)
            or (end_date and row_date > end_date)
            or (status and row.get("status") != status)
            or (category and row.get("category") != category)
            or (account_id and row.get("account_id") != account_id)
            or (currency and row.get("currency_original") != currency)
        )

    def get_by_id(self, txn_id: str) -> dict[str, Any] | None:
        for row in self._rows:
            if row.get("txn_id") == txn_id:
                return dict(row)
        return None

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
        matched = [
            r
            for r in self._rows
            if self._matches(
                r,
                start_date=start_date,
                end_date=end_date,
                status=status,
                category=category,
                account_id=account_id,
                currency=currency,
            )
        ]
        # Deterministic ordering — newest first.
        matched.sort(key=lambda r: r["date"], reverse=True)
        return [dict(r) for r in matched[offset : offset + limit]]

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
        return sum(
            1
            for r in self._rows
            if self._matches(
                r,
                start_date=start_date,
                end_date=end_date,
                status=status,
                category=category,
                account_id=account_id,
                currency=currency,
            )
        )

    def get_suspicious(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        susp = [dict(r) for r in self._rows if r.get("is_suspicious")]
        susp.sort(key=lambda r: r["date"], reverse=True)
        return susp[offset : offset + limit]

    def count_suspicious(self) -> int:
        return sum(1 for r in self._rows if r.get("is_suspicious"))

    def compute_summary(self) -> dict[str, Any]:
        if not self._rows:
            return {
                "total_transactions": 0,
                "total_amount_inr": 0.0,
                "by_status": {},
                "by_category": {},
                "by_currency_original": {},
            }
        by_status: dict[str, int] = {}
        by_category: dict[str, int] = {}
        by_currency: dict[str, int] = {}
        total = 0.0
        for r in self._rows:
            total += float(r.get("amount_inr", 0.0))
            s = r.get("status") or "UNKNOWN"
            by_status[s] = by_status.get(s, 0) + 1
            c = r.get("category") or "UNKNOWN"
            by_category[c] = by_category.get(c, 0) + 1
            cur = r.get("currency_original") or "UNKNOWN"
            by_currency[cur] = by_currency.get(cur, 0) + 1
        return {
            "total_transactions": len(self._rows),
            "total_amount_inr": round(total, 2),
            "by_status": by_status,
            "by_category": by_category,
            "by_currency_original": by_currency,
        }
