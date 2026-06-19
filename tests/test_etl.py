"""Unit tests for the ETL pipeline.

See `instruction.md` section 10 for coverage requirements. Each rule from
section 4 has at least one dedicated test below.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from app.etl import (
    EXCHANGE_RATES_TO_INR,
    ETLResult,
    _is_suspicious,
    _normalize_currency,
    _normalize_status,
    _parse_amount,
    _parse_date,
    run_etl,
)

# ----- Pure helper tests -----------------------------------------------------


class TestParseDate:
    def test_dd_mm_yyyy(self) -> None:
        assert _parse_date("04-09-2024").isoformat() == "2024-09-04"

    def test_yyyy_slash_mm_dd(self) -> None:
        assert _parse_date("2024/02/05").isoformat() == "2024-02-05"

    def test_yyyy_dash_mm_dd(self) -> None:
        assert _parse_date("2024-07-15").isoformat() == "2024-07-15"

    def test_empty_returns_none(self) -> None:
        assert _parse_date("") is None

    def test_none_returns_none(self) -> None:
        assert _parse_date(None) is None

    def test_garbage_returns_none(self) -> None:
        assert _parse_date("not-a-date") is None


class TestParseAmount:
    def test_plain_float(self) -> None:
        assert _parse_amount("11325.79") == 11325.79

    def test_dollar_prefix(self) -> None:
        assert _parse_amount("$11325.79") == 11325.79

    def test_with_commas(self) -> None:
        assert _parse_amount("1,234,567.89") == 1_234_567.89

    def test_zero_returns_none(self) -> None:
        # Zero amounts are treated as invalid per instruction 4.2.
        assert _parse_amount("0") is None

    def test_negative_returns_none(self) -> None:
        assert _parse_amount("-100.00") is None

    def test_garbage_returns_none(self) -> None:
        assert _parse_amount("abc") is None

    def test_whitespace_stripped(self) -> None:
        assert _parse_amount("  500.00  ") == 500.00


class TestNormalizeCurrency:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("INR", "INR"),
            ("inr", "INR"),
            ("Inr", "INR"),
            ("usd", "USD"),
            ("USD", "USD"),
        ],
    )
    def test_valid_codes_uppercased(self, raw: str, expected: str) -> None:
        assert _normalize_currency(raw) == expected

    def test_unknown_returns_none(self) -> None:
        assert _normalize_currency("XYZ") is None

    def test_empty_returns_none(self) -> None:
        assert _normalize_currency("") is None


class TestNormalizeStatus:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("SUCCESS", "SUCCESS"),
            ("success", "SUCCESS"),
            ("Success", "SUCCESS"),
            ("FAILED", "FAILED"),
            ("failed", "FAILED"),
            ("PENDING", "PENDING"),
            ("pending", "PENDING"),
        ],
    )
    def test_valid_statuses(self, raw: str, expected: str) -> None:
        assert _normalize_status(raw) == expected

    def test_unknown_returns_none(self) -> None:
        assert _normalize_status("UNKNOWN") is None

    def test_empty_returns_none(self) -> None:
        assert _normalize_status("") is None


class TestIsSuspicious:
    def test_high_amount_flagged(self) -> None:
        assert _is_suspicious(150_000.0, "") is True

    def test_low_amount_not_flagged(self) -> None:
        assert _is_suspicious(500.0, "") is False

    def test_suspicious_note_flagged(self) -> None:
        assert _is_suspicious(100.0, "SUSPICIOUS activity") is True

    def test_lowercase_suspicious_note_flagged(self) -> None:
        assert _is_suspicious(100.0, "this looks suspicious") is True

    def test_threshold_exact_not_flagged(self) -> None:
        # Strictly greater than, per instruction 4.7.
        assert _is_suspicious(100_000.0, "") is False


# ----- Integration tests on the full pipeline -------------------------------


def test_run_etl_returns_typed_result(sample_csv_path: object) -> None:
    result = run_etl(sample_csv_path)
    assert isinstance(result, ETLResult)
    assert isinstance(result.clean_df, pd.DataFrame)


def test_dates_are_iso_normalized(sample_csv_path: object) -> None:
    result = run_etl(sample_csv_path)
    # Every clean row's date must be ISO-formatted (YYYY-MM-DD).
    for d in result.clean_df["date"]:
        assert pd.Timestamp(d).strftime("%Y-%m-%d") == d


def test_currency_conversion_to_inr(sample_csv_path: object) -> None:
    result = run_etl(sample_csv_path)
    # The $11325.79 USD row must end up at 11325.79 * 83.2 INR
    usd_row = result.clean_df[result.clean_df["txn_id"] == "TXN1001"].iloc[0]
    expected = round(11325.79 * EXCHANGE_RATES_TO_INR["USD"], 2)
    assert usd_row["amount_inr"] == expected


def test_lowercase_currency_normalized(sample_csv_path: object) -> None:
    result = run_etl(sample_csv_path)
    # The regenerated row uses 'inr' -> must become 'INR' in output.
    gen_rows = result.clean_df[result.clean_df["txn_id"].str.startswith("TXN_GEN_")]
    assert all(gen_rows["currency_original"] == "INR")


def test_lowercase_status_normalized(sample_csv_path: object) -> None:
    result = run_etl(sample_csv_path)
    statuses = set(result.clean_df["status"].dropna().unique())
    # Only canonical statuses (or empty) should appear.
    assert statuses <= {"SUCCESS", "FAILED", "PENDING", ""}


def test_missing_txn_id_is_regenerated(sample_csv_path: object) -> None:
    result = run_etl(sample_csv_path)
    gen_ids = [t for t in result.clean_df["txn_id"] if t.startswith("TXN_GEN_")]
    # Fixture has 4 rows missing txn_id:
    #   - bad-date row  -> quarantined
    #   - missing-account row -> quarantined
    #   - two others are valid -> regenerated and kept
    assert len(gen_ids) == 2


def test_duplicates_are_quarantined(sample_csv_path: object) -> None:
    result = run_etl(sample_csv_path)
    dup_reasons = [q.reason for q in result.quarantine]
    # Fixture has TXN1000 repeated (2nd occurrence) and TXN1001 repeated
    # (2nd occurrence) -> 2 duplicates quarantined.
    assert dup_reasons.count("duplicate") == 2


def test_missing_account_id_is_quarantined(sample_csv_path: object) -> None:
    result = run_etl(sample_csv_path)
    reasons = [q.reason for q in result.quarantine]
    assert "missing account_id" in reasons


def test_unparseable_date_is_quarantined(sample_csv_path: object) -> None:
    result = run_etl(sample_csv_path)
    reasons = [q.reason for q in result.quarantine]
    assert any("unparseable date" in r for r in reasons)


def test_high_amount_marked_suspicious(sample_csv_path: object) -> None:
    result = run_etl(sample_csv_path)
    susp = result.clean_df[result.clean_df["txn_id"] == "TXN1002"]
    assert len(susp) == 1
    assert bool(susp.iloc[0]["is_suspicious"]) is True


def test_suspicious_note_marked_suspicious(sample_csv_path: object) -> None:
    # We inject a known suspicious-note row via the fixture's 9th data line
    # (which also has no account_id -> quarantined). Let's add an explicit
    # small-amount suspicious-note row to verify the rule via the real CSV.
    import tempfile
    from pathlib import Path

    csv = (
        "txn_id,date,merchant,amount,currency,status,category,account_id,notes\n"
        "TXN9001,2024/03/01,TestMart,500.00,INR,SUCCESS,Test,ACC999,SUSPICIOUS\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        f.write(csv)
        path = Path(f.name)
    try:
        result = run_etl(path)
        row = result.clean_df[result.clean_df["txn_id"] == "TXN9001"].iloc[0]
        assert bool(row["is_suspicious"]) is True
    finally:
        path.unlink()


def test_summary_structure(sample_csv_path: object) -> None:
    result = run_etl(sample_csv_path)
    s = result.summary
    assert set(s.keys()) == {
        "total_transactions",
        "total_amount_inr",
        "by_status",
        "by_category",
        "by_currency_original",
    }
    assert s["total_transactions"] == len(result.clean_df)
    assert s["total_amount_inr"] > 0


def test_quarantine_carries_raw_row(sample_csv_path: object) -> None:
    result = run_etl(sample_csv_path)
    assert result.quarantine, "expected at least one quarantined row"
    q = result.quarantine[0]
    assert isinstance(q.row_index, int)
    assert q.row_index >= 2  # +1 for header, +1 for 1-based
    assert isinstance(q.raw, dict)
    assert "txn_id" in q.raw
    assert isinstance(q.reason, str)


def test_real_csv_runs_without_error(real_csv_path: object) -> None:
    """Smoke test: the real assignment CSV must not crash the pipeline."""
    result = run_etl(real_csv_path)
    # Sanity bounds — we should clean a majority of the ~97 rows.
    assert len(result.clean_df) > 50
    assert isinstance(result.summary, dict)
    # Verify it's JSON-serializable (will be used by /summary endpoint).
    json.dumps(result.summary)
