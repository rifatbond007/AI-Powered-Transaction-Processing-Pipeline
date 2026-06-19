"""ETL pipeline for the transactions dataset (PDF §5(a)).

Reads a raw CSV and normalises it for downstream anomaly + LLM stages.
Bad rows are quarantined; missing categories are filled with the literal
``"Uncategorised"`` per the spec (not the empty string).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


UNCATEGORISED = "Uncategorised"

#: Recognized date formats, tried in order. First match wins.
_DATE_FORMATS: tuple[str, ...] = (
    "%d-%m-%Y",  # 04-09-2024
    "%Y/%m/%d",  # 2024/02/05
    "%Y-%m-%d",  # 2024-07-15
    "%d/%m/%Y",  # 25/12/2024
    "%Y-%m-%d %H:%M:%S",
)

#: Regex matching currency symbols / commas / whitespace in amount strings.
_AMOUNT_CLEAN_RE = re.compile(r"[\s,$€£¥]+")


@dataclass
class QuarantineRow:
    """A row that was rejected during cleaning, with the reason."""

    row_index: int
    raw: dict[str, Any]
    reason: str


@dataclass
class CleanResult:
    """The outcome of running :func:`run_etl`."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    quarantine: list[QuarantineRow] = field(default_factory=list)
    row_count_raw: int = 0


# ----- Internal helpers ------------------------------------------------------


def _parse_date(value: Any) -> date | None:
    """Parse a date string in any of the supported formats."""
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(value: Any) -> float | None:
    """Strip currency symbols/commas and parse a numeric amount.

    Returns ``None`` for unparseable, zero, or negative values.
    """
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    cleaned = _AMOUNT_CLEAN_RE.sub("", text)
    try:
        amount = float(Decimal(cleaned))
    except (InvalidOperation, ValueError):
        return None
    if amount <= 0:
        return None
    return round(amount, 2)


# ----- Public API ------------------------------------------------------------


def run_etl(source: str | Path) -> CleanResult:
    """Run cleaning on a CSV file and return a :class:`CleanResult`.

    The output dict shape is the contract for the anomaly + LLM stages.
    Each row has at least::

        {
            "txn_id": str,
            "date": "YYYY-MM-DD",
            "merchant": str,
            "amount": float,
            "currency": str,           # uppercase
            "status": str,             # uppercase, "" if missing
            "category": str,           # "Uncategorised" if missing
            "account_id": str,
        }
    """
    logger.info("Loading CSV from %s", source)
    raw_df = pd.read_csv(source, dtype=str, keep_default_na=False)
    logger.info("Loaded %d raw rows", len(raw_df))

    rows: list[dict[str, Any]] = []
    quarantine: list[QuarantineRow] = []
    seen: set[tuple[str, date, float, str]] = set()

    for idx, row in raw_df.iterrows():
        raw = row.to_dict()
        row_index = int(idx) + 2  # +2 = header + 1-based for humans

        # --- Required: account_id ---
        account_id = str(row.get("account_id", "")).strip()
        if not account_id:
            quarantine.append(QuarantineRow(row_index, raw, "missing account_id"))
            continue

        # --- Date ---
        parsed_date = _parse_date(row.get("date"))
        if parsed_date is None:
            quarantine.append(
                QuarantineRow(row_index, raw, f"unparseable date: {row.get('date')!r}")
            )
            continue

        # --- Amount ---
        amount = _parse_amount(row.get("amount"))
        if amount is None:
            quarantine.append(
                QuarantineRow(row_index, raw, f"unparseable amount: {row.get('amount')!r}")
            )
            continue

        # --- Currency (uppercase) ---
        currency = str(row.get("currency", "")).strip().upper()
        if not currency:
            quarantine.append(
                QuarantineRow(row_index, raw, f"invalid currency: {row.get('currency')!r}")
            )
            continue

        # --- Status (uppercase; empty allowed) ---
        status = str(row.get("status", "")).strip().upper()

        # --- Category: missing -> "Uncategorised" (PDF §5(a)) ---
        category = str(row.get("category", "")).strip() or UNCATEGORISED

        # --- txn_id: regenerate if missing ---
        txn_id = str(row.get("txn_id", "")).strip()
        if not txn_id:
            txn_id = f"TXN_GEN_{idx}"
            logger.info("Row %d: regenerated missing txn_id as %s", row_index, txn_id)

        # --- Duplicates (per-job scope, but we also dedupe within the input) ---
        dedup_key = (txn_id, parsed_date, amount, account_id)
        if dedup_key in seen:
            quarantine.append(QuarantineRow(row_index, raw, "duplicate"))
            continue
        seen.add(dedup_key)

        # --- Merchant ---
        merchant = str(row.get("merchant", "")).strip()

        rows.append(
            {
                "txn_id": txn_id,
                "date": parsed_date.isoformat(),
                "merchant": merchant,
                "amount": amount,
                "currency": currency,
                "status": status,
                "category": category,
                "account_id": account_id,
            }
        )

    logger.info("ETL complete: %d clean rows, %d quarantined", len(rows), len(quarantine))
    return CleanResult(rows=rows, quarantine=quarantine, row_count_raw=len(raw_df))
