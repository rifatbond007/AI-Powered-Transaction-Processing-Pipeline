"""Pydantic v2 schemas for the public API.

Kept deliberately small and JSON-friendly — amounts are floats (in INR),
dates are ISO strings, booleans are native.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class Transaction(BaseModel):
    """A single cleaned transaction returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    txn_id: str
    date: str
    merchant: str
    amount_original: float
    currency_original: str
    amount_inr: float
    status: str
    category: str
    account_id: str
    notes: str
    is_suspicious: bool


class TransactionPage(BaseModel):
    """A paginated list of transactions with total count."""

    items: list[Transaction]
    total: int
    limit: int
    offset: int


class Summary(BaseModel):
    """Aggregate summary statistics."""

    total_transactions: int
    total_amount_inr: float
    by_status: dict[str, int]
    by_category: dict[str, int]
    by_currency_original: dict[str, int]


class HealthResponse(BaseModel):
    """Health-check response."""

    status: str = "ok"


# Query-parameter aliases keep the route signatures tidy.
LimitQuery = Annotated[int, Field(ge=1, le=500, description="Page size (1-500)")]
OffsetQuery = Annotated[int, Field(ge=0, description="Number of rows to skip")]


def transaction_to_dict(t: Any) -> dict[str, Any]:
    """Helper for tests that need to compare dicts against the schema."""
    return Transaction.model_validate(t).model_dump()
