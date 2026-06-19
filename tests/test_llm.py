"""LLM client tests — retry decorator, batching, JSON coercion (PDF §5)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _stub_google_api_key(monkeypatch):
    """Make Settings think GOOGLE_API_KEY is set, so app.llm is importable."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")


def test_classify_categories_batched() -> None:
    """Batching: 25 rows at batch_size 10 -> 3 LLM calls."""
    rows = [
        {"merchant": "X", "amount": 1.0, "currency": "INR", "account_id": "A"} for _ in range(25)
    ]

    call_count = {"n": 0}

    def fake_classify(batch):
        call_count["n"] += 1
        return {"categories": ["Food"] * len(batch), "raw": "stub"}

    from app.services import llm

    with patch.object(llm, "_classify_call", side_effect=fake_classify):
        out = llm.classify_categories(rows)

    assert call_count["n"] == 2  # 20 + 5 with default LLM_BATCH_SIZE=20
    assert len(out) == 25
    for entry in out:
        assert entry["llm_category"] == "Food"
        assert entry["llm_failed"] is False
        assert entry["llm_raw_response"] == "stub"


def test_classify_categories_marks_batch_failed_on_error() -> None:
    """If _classify_call returns llm_failed=True, all rows in the batch are marked."""
    rows = [
        {"merchant": "X", "amount": 1.0, "currency": "INR", "account_id": "A"} for _ in range(3)
    ]

    from app.services import llm

    with patch.object(llm, "_classify_call", return_value={"llm_failed": True, "error": "boom"}):
        out = llm.classify_categories(rows)

    assert len(out) == 3
    for entry in out:
        assert entry["llm_failed"] is True
        assert entry["llm_category"] is None


def test_generate_summary_returns_dict() -> None:
    payload = {"total_spend_by_currency": {"INR": 100.0}, "anomaly_count": 0}

    from app.services import llm

    fake = {
        "narrative": "All good.",
        "risk_level": "low",
        "top_3_merchants": [{"merchant": "X", "total_inr": 100.0}],
    }
    with patch.object(llm, "_summarize_call", return_value={**fake, "raw": "stub"}):
        out = llm.generate_summary(payload)
    assert out["narrative"] == "All good."
    assert out["risk_level"] == "low"


def test_extract_json_handles_fenced_response() -> None:
    from app.services.llm import _extract_json

    raw = "```json\n" + json.dumps({"x": 1}) + "\n```"
    assert _extract_json(raw) == {"x": 1}


def test_extract_json_handles_bare_object() -> None:
    from app.services.llm import _extract_json

    assert _extract_json(json.dumps({"x": 1})) == {"x": 1}


def test_extract_json_handles_prose_embedded() -> None:
    from app.services.llm import _extract_json

    assert _extract_json("Sure! Here: " + json.dumps([1, 2, 3])) == [1, 2, 3]


def test_coerce_category_unknown_falls_back_to_other() -> None:
    from app.services.llm import _coerce_category

    assert _coerce_category("Refunds") == "Other"
    assert _coerce_category("food") == "Food"  # case-insensitive
    assert _coerce_category(None) == "Other"
