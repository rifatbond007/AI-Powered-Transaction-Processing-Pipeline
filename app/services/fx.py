"""Currency conversion (static rates per assignment spec)."""

from __future__ import annotations

#: Exchange rates to INR. Kept from the original implementation — the PDF
#: requires `total_spend_inr` in the JobSummary output but does not call
#: out a source for FX rates, so static rates are appropriate.
EXCHANGE_RATES_TO_INR: dict[str, float] = {
    "INR": 1.0,
    "USD": 83.2,
    "EUR": 90.5,
    "GBP": 107.8,
}


def to_inr(amount: float, currency: str) -> float:
    """Convert ``amount`` in ``currency`` to INR. Returns 0.0 for unknown codes."""
    rate = EXCHANGE_RATES_TO_INR.get(currency.upper(), 0.0)
    return round(amount * rate, 2)
