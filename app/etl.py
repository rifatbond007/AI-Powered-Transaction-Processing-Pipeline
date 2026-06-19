"""ETL pipeline for the transactions dataset.

Reads a dirty CSV, normalizes dates / currencies / status, converts all amounts
to INR, drops duplicates, flags suspicious rows, and returns a structured
result with a quarantine list for rejected rows.

See `instruction.md` section 4 for the full ruleset.
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


# ----- Constants -------------------------------------------------------------

#: Exchange rates to INR (static per spec; see instruction.md section 4.3).
EXCHANGE_RATES_TO_INR: dict[str, float] = {
    "INR": 1.0,
    "USD": 83.2,
    "EUR": 90.5,
    "GBP": 107.8,
}

VALID_CURRENCIES: set[str] = set(EXCHANGE_RATES_TO_INR.keys())

#: Canonical status values (post-normalization).
VALID_STATUSES: set[str] = {"SUCCESS", "FAILED", "PENDING"}

#: Transactions above this INR amount are flagged suspicious.
SUSPICIOUS_AMOUNT_THRESHOLD_INR: float = 100_000.0

#: Regex matching currency symbols / commas / whitespace in amount strings.
_AMOUNT_CLEAN_RE = re.compile(r"[\s,$€£¥]+")

#: Recognized date formats, tried in order. First match wins.
_DATE_FORMATS: tuple[str, ...] = (
    "%d-%m-%Y",  # 04-09-2024
    "%Y/%m/%d",  # 2024/02/05
    "%Y-%m-%d",  # 2024-07-15
    "%d/%m/%Y",  # 25/12/2024
    "%Y-%m-%d %H:%M:%S",
)


# ----- Result types ----------------------------------------------------------


@dataclass
class QuarantineRow:
    """A row that was rejected during cleaning, with the reason."""

    row_index: int
    raw: dict[str, Any]
    reason: str


@dataclass
class ETLResult:
    """The outcome of running :func:`run_etl`."""

    clean_df: pd.DataFrame
    quarantine: list[QuarantineRow] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


# ----- Internal helpers ------------------------------------------------------


def _parse_date(value: Any) -> date | None:
    """Parse a date string in any of the supported formats.

    Returns ``None`` if no format matches.
    """
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
    # Round to 2 decimal places for currency-style precision.
    return round(amount, 2)


def _normalize_currency(value: Any) -> str | None:
    """Uppercase a currency code and validate it against the known set."""
    if pd.isna(value):
        return None
    code = str(value).strip().upper()
    return code if code in VALID_CURRENCIES else None


def _normalize_status(value: Any) -> str | None:
    """Uppercase a status and validate it against the known set."""
    if pd.isna(value):
        return None
    code = str(value).strip().upper()
    return code if code in VALID_STATUSES else None


def _is_suspicious(amount_inr: float, notes: str) -> bool:
    """A row is suspicious if amount > threshold or notes contain SUSPICIOUS."""
    if amount_inr > SUSPICIOUS_AMOUNT_THRESHOLD_INR:
        return True
    if notes and "SUSPICIOUS" in notes.upper():
        return True
    return False


def _build_summary(df: pd.DataFrame) -> dict[str, Any]:
    """Build the aggregate summary dictionary exposed via /summary."""
    if df.empty:
        return {
            "total_transactions": 0,
            "total_amount_inr": 0.0,
            "by_status": {},
            "by_category": {},
            "by_currency_original": {},
        }

    by_status = df.groupby("status").size().to_dict()
    by_category = df.groupby("category").size().to_dict()
    # Original currency counts (pre-conversion), still useful for audits.
    by_currency = df.groupby("currency_original").size().to_dict()
    return {
        "total_transactions": int(len(df)),
        "total_amount_inr": round(float(df["amount_inr"].sum()), 2),
        "by_status": {k: int(v) for k, v in by_status.items()},
        "by_category": {k: int(v) for k, v in by_category.items()},
        "by_currency_original": {k: int(v) for k, v in by_currency.items()},
    }


# ----- Public API ------------------------------------------------------------


def run_etl(source: str | Path) -> ETLResult:
    """Run the full ETL pipeline on a CSV file and return an :class:`ETLResult`.

    The pipeline is intentionally non-destructive: rejected rows are captured
    in ``result.quarantine`` rather than silently dropped, so operators can
    audit every decision.
    """
    logger.info("Loading CSV from %s", source)
    raw_df = pd.read_csv(source, dtype=str, keep_default_na=False)
    logger.info("Loaded %d raw rows", len(raw_df))

    clean_records: list[dict[str, Any]] = []
    quarantine: list[QuarantineRow] = []
    seen_keys: set[tuple[str, date, float, str]] = set()

    for idx, row in raw_df.iterrows():
        raw = row.to_dict()
        row_index = int(idx) + 2  # +2 = header row + 1-based for humans

        # --- Required: account_id ---
        account_id = str(row.get("account_id", "")).strip()
        if not account_id:
            quarantine.append(QuarantineRow(row_index, raw, "missing account_id"))
            continue

        # --- Date (auto-detect format) ---
        parsed_date = _parse_date(row.get("date"))
        if parsed_date is None:
            quarantine.append(
                QuarantineRow(row_index, raw, f"unparseable date: {row.get('date')!r}")
            )
            continue

        # --- Amount (strip $, commas) ---
        amount = _parse_amount(row.get("amount"))
        if amount is None:
            quarantine.append(
                QuarantineRow(row_index, raw, f"unparseable amount: {row.get('amount')!r}")
            )
            continue

        # --- Currency ---
        currency = _normalize_currency(row.get("currency"))
        if currency is None:
            quarantine.append(
                QuarantineRow(row_index, raw, f"invalid currency: {row.get('currency')!r}")
            )
            continue

        # --- Status (optional) ---
        status = _normalize_status(row.get("status"))
        # status missing is allowed; treat as empty string in output.

        # --- txn_id: regenerate if missing ---
        txn_id = str(row.get("txn_id", "")).strip()
        if not txn_id:
            txn_id = f"TXN_GEN_{idx}"
            logger.info("Row %d: regenerated missing txn_id as %s", row_index, txn_id)

        # --- Duplicates ---
        dedup_key = (txn_id, parsed_date, amount, account_id)
        if dedup_key in seen_keys:
            quarantine.append(QuarantineRow(row_index, raw, "duplicate"))
            continue
        seen_keys.add(dedup_key)

        # --- Convert to INR ---
        amount_inr = round(amount * EXCHANGE_RATES_TO_INR[currency], 2)

        # --- Optional fields ---
        merchant = str(row.get("merchant", "")).strip()
        category = str(row.get("category", "")).strip()
        notes = str(row.get("notes", "")).strip()

        clean_records.append(
            {
                "txn_id": txn_id,
                "date": parsed_date.isoformat(),
                "merchant": merchant,
                "amount_original": amount,
                "currency_original": currency,
                "amount_inr": amount_inr,
                "status": status or "",
                "category": category,
                "account_id": account_id,
                "notes": notes,
                "is_suspicious": _is_suspicious(amount_inr, notes),
            }
        )

    clean_df = pd.DataFrame(clean_records)
    summary = _build_summary(clean_df)

    logger.info(
        "ETL complete: %d clean rows, %d quarantined",
        len(clean_df),
        len(quarantine),
    )
    return ETLResult(clean_df=clean_df, quarantine=quarantine, summary=summary)


def write_summary_json(result: ETLResult, destination: str | Path) -> None:
    """Write the ETL summary to a JSON file (helper used by scripts/init_db)."""
    import json

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": result.summary,
        "quarantine_count": len(result.quarantine),
    }
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True))
    logger.info("Wrote summary to %s", destination)
