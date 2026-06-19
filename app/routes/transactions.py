"""Routes for the /transactions and /suspicious endpoints."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import get_store
from app.schemas import LimitQuery, OffsetQuery, Transaction, TransactionPage
from app.store import TransactionStore

router = APIRouter(tags=["transactions"])


@router.get("/transactions", response_model=TransactionPage)
def list_transactions(
    start_date: date | None = Query(default=None, description="Inclusive start date (YYYY-MM-DD)"),
    end_date: date | None = Query(default=None, description="Inclusive end date (YYYY-MM-DD)"),
    status_filter: str | None = Query(default=None, alias="status"),
    category: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    currency: str | None = Query(
        default=None, description="Original currency code (INR/USD/EUR/GBP)"
    ),
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
    store: TransactionStore = Depends(get_store),
) -> TransactionPage:
    """List transactions with optional filters and pagination."""
    items = store.query(
        start_date=start_date,
        end_date=end_date,
        status=status_filter,
        category=category,
        account_id=account_id,
        currency=currency,
        limit=limit,
        offset=offset,
    )
    total = store.count(
        start_date=start_date,
        end_date=end_date,
        status=status_filter,
        category=category,
        account_id=account_id,
        currency=currency,
    )
    return TransactionPage(
        items=[Transaction.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/transactions/{txn_id}", response_model=Transaction)
def get_transaction(
    txn_id: str,
    store: TransactionStore = Depends(get_store),
) -> Transaction:
    """Fetch a single transaction by id."""
    row = store.get_by_id(txn_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction {txn_id!r} not found",
        )
    return Transaction.model_validate(row)


@router.get("/suspicious", response_model=TransactionPage)
def list_suspicious(
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
    store: TransactionStore = Depends(get_store),
) -> TransactionPage:
    """List transactions flagged as suspicious (high amount or note)."""
    items = store.get_suspicious(limit=limit, offset=offset)
    total = store.count_suspicious()
    return TransactionPage(
        items=[Transaction.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )
