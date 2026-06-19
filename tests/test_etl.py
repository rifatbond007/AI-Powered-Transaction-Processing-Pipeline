"""ETL cleaning tests (PDF §5(a))."""

from __future__ import annotations

from app.etl import run_etl


def test_clean_rows_count(sample_csv_path) -> None:
    """All in-spec rows survive; bad rows are quarantined, not dropped."""
    result = run_etl(sample_csv_path)
    # 10 raw rows: 1 missing account_id, 1 unparseable date, 2 duplicates (TXN1000 and TXN1001) => 6 clean
    assert result.row_count_raw == 10
    assert len(result.rows) == 6
    assert len(result.quarantine) == 4


def test_currency_uppercased(sample_csv_path) -> None:
    result = run_etl(sample_csv_path)
    by_currency = {r["currency"] for r in result.rows}
    assert by_currency == {"INR", "USD"}  # all lowercase 'inr' should be normalised
    assert all(r["currency"] == r["currency"].upper() for r in result.rows)


def test_missing_category_filled(sample_csv_path) -> None:
    result = run_etl(sample_csv_path)
    uncategorised = [r for r in result.rows if r["category"] == "Uncategorised"]
    # The "Jio Recharge" row had an empty category in the input.
    assert any(r["merchant"] == "Jio Recharge" for r in uncategorised)


def test_missing_txn_id_regenerated(sample_csv_path) -> None:
    result = run_etl(sample_csv_path)
    generated = [r for r in result.rows if r["txn_id"].startswith("TXN_GEN_")]
    assert len(generated) >= 1


def test_duplicate_quarantined(sample_csv_path) -> None:
    result = run_etl(sample_csv_path)
    dup_reasons = [q.reason for q in result.quarantine]
    assert "duplicate" in dup_reasons


def test_unparseable_date_quarantined(sample_csv_path) -> None:
    result = run_etl(sample_csv_path)
    reasons = [q.reason for q in result.quarantine]
    assert any("unparseable date" in r for r in reasons)


def test_missing_account_id_quarantined(sample_csv_path) -> None:
    result = run_etl(sample_csv_path)
    reasons = [q.reason for q in result.quarantine]
    assert "missing account_id" in reasons


def test_amount_strips_currency_symbol(sample_csv_path) -> None:
    result = run_etl(sample_csv_path)
    swiggy = next(r for r in result.rows if r["merchant"] == "Swiggy")
    # "$11325.79" -> 11325.79
    assert swiggy["amount"] == 11325.79
