"""Anomaly detection tests (PDF §5(b))."""

from __future__ import annotations

from app.services.anomaly import (
    DOMESTIC_BRANDS,
    REASON_AMOUNT_3X_MEDIAN,
    REASON_USD_DOMESTIC,
    flag_anomalies,
)


def test_empty_rows_returns_empty() -> None:
    assert flag_anomalies([]) == []


def test_amount_3x_median_fires() -> None:
    rows = [
        {"txn_id": "a", "amount": 100.0, "account_id": "ACC1", "currency": "INR", "merchant": "X"},
        {"txn_id": "b", "amount": 100.0, "account_id": "ACC1", "currency": "INR", "merchant": "X"},
        {"txn_id": "c", "amount": 100.0, "account_id": "ACC1", "currency": "INR", "merchant": "X"},
        {"txn_id": "d", "amount": 1000.0, "account_id": "ACC1", "currency": "INR", "merchant": "X"},
    ]
    out = flag_anomalies(rows)
    assert out[-1]["is_anomaly"] is True
    assert out[-1]["anomaly_reason"] == REASON_AMOUNT_3X_MEDIAN
    for r in out[:-1]:
        assert r["is_anomaly"] is False


def test_single_row_account_never_trips_median_rule() -> None:
    rows = [
        {"txn_id": "x", "amount": 50.0, "account_id": "ACC1", "currency": "INR", "merchant": "X"}
    ]
    out = flag_anomalies(rows)
    assert out[0]["is_anomaly"] is False


def test_usd_domestic_brand_flagged() -> None:
    rows = [
        {
            "txn_id": "s",
            "amount": 100.0,
            "account_id": "ACC1",
            "currency": "USD",
            "merchant": "Swiggy",
        }
    ]
    out = flag_anomalies(rows)
    assert out[0]["is_anomaly"] is True
    assert out[0]["anomaly_reason"] == REASON_USD_DOMESTIC


def test_usd_non_domestic_brand_not_flagged() -> None:
    rows = [
        {
            "txn_id": "s",
            "amount": 100.0,
            "account_id": "ACC1",
            "currency": "USD",
            "merchant": "Amazon",
        }
    ]
    out = flag_anomalies(rows)
    assert out[0]["is_anomaly"] is False


def test_inr_domestic_brand_not_flagged_by_rule_b() -> None:
    rows = [
        {
            "txn_id": "s",
            "amount": 100.0,
            "account_id": "ACC1",
            "currency": "INR",
            "merchant": "Ola",
        }
    ]
    out = flag_anomalies(rows)
    assert out[0]["is_anomaly"] is False


def test_both_rules_join_reasons() -> None:
    rows = [
        {"txn_id": "a", "amount": 100.0, "account_id": "ACC1", "currency": "INR", "merchant": "X"},
        {"txn_id": "b", "amount": 100.0, "account_id": "ACC1", "currency": "INR", "merchant": "X"},
        # USD + Swiggy AND > 3x median (100)
        {
            "txn_id": "c",
            "amount": 500.0,
            "account_id": "ACC1",
            "currency": "USD",
            "merchant": "IRCTC",
        },
    ]
    out = flag_anomalies(rows)
    assert out[-1]["is_anomaly"] is True
    assert "+" in out[-1]["anomaly_reason"]
    assert REASON_AMOUNT_3X_MEDIAN in out[-1]["anomaly_reason"]
    assert REASON_USD_DOMESTIC in out[-1]["anomaly_reason"]


def test_all_three_domestic_brands() -> None:
    for brand in DOMESTIC_BRANDS:
        rows = [
            {
                "txn_id": "x",
                "amount": 10.0,
                "account_id": "A",
                "currency": "USD",
                "merchant": brand,
            }
        ]
        assert flag_anomalies(rows)[0]["is_anomaly"] is True
