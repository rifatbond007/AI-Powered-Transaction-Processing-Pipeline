"""Anomaly detection rules (PDF §5(b)).

Two rules, OR'd per row:

- **amount_3x_median** — row amount > 3x the median amount for the same
  ``account_id``. Implemented via pandas groupby; single-row accounts
  never trip this rule (their amount equals their median).
- **usd_domestic** — ``currency == "USD"`` AND merchant in the
  domestic-only brand set ``{Swiggy, Ola, IRCTC}``.

Both rules can fire on the same row; reasons are joined with ``+``.
``is_anomaly`` is True iff any reason fired.

The module is pure (no DB, no LLM, no I/O) so it can be unit-tested
without any of the heavier machinery.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

#: Domestic-only brands the spec calls out (PDF §5(b)).
DOMESTIC_BRANDS: frozenset[str] = frozenset({"Swiggy", "Ola", "IRCTC"})

REASON_AMOUNT_3X_MEDIAN = "amount_3x_median"
REASON_USD_DOMESTIC = "usd_domestic"


def flag_anomalies(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a new list of rows with ``is_anomaly`` and ``anomaly_reason`` set.

    Input rows must each have ``amount`` (numeric), ``account_id`` (str),
    ``currency`` (str), and ``merchant`` (str). The function does not
    mutate the input rows.
    """
    if not rows:
        return []

    df = pd.DataFrame(rows).copy()
    df["_anomaly_reasons"] = [[] for _ in range(len(df))]

    # Rule A — 3x account median. Skipped silently for empty / single-row
    # accounts because the median equals the value and `amount > 3 * amount`
    # is always False for positive amounts.
    if "account_id" in df.columns and "amount" in df.columns:
        medians = df.groupby("account_id")["amount"].transform("median")
        rule_a = df["amount"] > 3 * medians
        for i, hit in enumerate(rule_a):
            if bool(hit):
                df.at[i, "_anomaly_reasons"].append(REASON_AMOUNT_3X_MEDIAN)

    # Rule B — USD + domestic brand.
    if {"currency", "merchant"}.issubset(df.columns):
        rule_b = (df["currency"].astype(str).str.upper() == "USD") & df["merchant"].isin(
            DOMESTIC_BRANDS
        )
        for i, hit in enumerate(rule_b):
            if bool(hit):
                df.at[i, "_anomaly_reasons"].append(REASON_USD_DOMESTIC)

    out: list[dict[str, Any]] = []
    for i, src in enumerate(rows):
        reasons = df.at[i, "_anomaly_reasons"]
        out.append(
            {
                **src,
                "is_anomaly": bool(reasons),
                "anomaly_reason": "+".join(reasons) if reasons else None,
            }
        )
    return out
