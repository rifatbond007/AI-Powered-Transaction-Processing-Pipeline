"""LLM client (PDF §5(c) to (e)).

The only file that imports :mod:`google.generativeai`. The public API:

- :func:`classify_categories` — batched classification of uncategorised rows.
- :func:`generate_summary` — single-call narrative + risk_level + top merchants.

Both functions are decorated with :func:`retry_llm` (3 attempts, exponential
backoff 1s/2s/4s). After 3 failures, the function returns
``{"llm_failed": True}`` so the caller can mark the batch and continue
(PDF §5(e)).

Mockability: tests rebind the module-level ``_classify`` and
``_summarize`` symbols, not the public names, so
``app.llm.classify_categories`` is always the public function and
patching the underlying call site is one ``monkeypatch.setattr`` away.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any, TypeVar

from app.config import get_settings

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Subset of categories the PDF allows (also used in app.worker).
PDF_CATEGORIES: tuple[str, ...] = (
    "Food",
    "Shopping",
    "Travel",
    "Transport",
    "Utilities",
    "Cash Withdrawal",
    "Entertainment",
    "Other",
)


# ----- Retry decorator ------------------------------------------------------ #


def retry_llm(fn: F) -> F:
    """Retry ``fn`` 3 times with exponential backoff (1s, 2s, 4s).

    Returns a wrapper that catches the LLM-specific exceptions below and
    returns ``{"llm_failed": True}`` once attempts are exhausted. The
    wrapped function must return a dict so the ``llm_failed`` flag can be
    merged in.
    """
    try:
        from tenacity import (
            Retrying,
            retry_if_exception_type,
            stop_after_attempt,
            wait_exponential,
        )
    except ImportError:  # pragma: no cover - tenacity is in requirements.txt
        return fn

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Lazy import so tests can monkeypatch the exceptions module.
        from google.api_core.exceptions import GoogleAPIError
        from google.generativeai.types import BlockedPromptException

        retryer = Retrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type((GoogleAPIError, BlockedPromptException)),
            reraise=False,
        )
        try:
            for attempt in retryer:
                with attempt:
                    return fn(*args, **kwargs)
        except Exception as e:  # tenacity will re-raise the last exc
            logger.warning("LLM call exhausted retries: %s", e)
            return {"llm_failed": True, "error": str(e)}
        return {"llm_failed": True}

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper  # type: ignore[return-value]


# ----- Gemini call (the only place the real client is touched) -------------- #


def _call_gemini(prompt: str, *, json_mode: bool = True) -> str:
    """Make a single Gemini call. Lazy-imports ``google.generativeai``."""
    settings = get_settings()
    if not settings.google_api_key:
        raise RuntimeError("GOOGLE_API_KEY is not set")

    import google.generativeai as genai

    genai.configure(api_key=settings.google_api_key)
    model = genai.GenerativeModel(settings.llm_model)
    generation_config = {"response_mime_type": "application/json"} if json_mode else None
    response = model.generate_content(prompt, generation_config=generation_config)
    return response.text or ""


# ----- JSON extraction & validation ----------------------------------------- #


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def _extract_json(text: str) -> Any:
    """Find a JSON object/array in ``text`` and parse it.

    Handles three common shapes from LLMs: bare JSON, markdown-fenced
    ```json ...```, and prose with a JSON object embedded.
    """
    text = text.strip()
    if not text:
        return None
    if text[0] in "[{":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Last resort: find the first { or [ and try to parse from there.
    for start_char, end_char in (("{", "}"), ("[", "]")):
        i = text.find(start_char)
        if i >= 0:
            j = text.rfind(end_char)
            if j > i:
                try:
                    return json.loads(text[i : j + 1])
                except json.JSONDecodeError:
                    pass
    return None


def _coerce_category(value: Any) -> str:
    """Map a model output string to one of PDF_CATEGORIES, defaulting to "Other"."""
    if not isinstance(value, str):
        return "Other"
    v = value.strip()
    for cat in PDF_CATEGORIES:
        if v.lower() == cat.lower():
            return cat
    return "Other"


# ----- Public functions (mockable via _classify / _summarize) --------------- #


@retry_llm
def _classify_call(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Single batched call to Gemini. Returns ``{"categories": [...]}``."""
    prompt = (
        "You are a financial transaction classifier. "
        "Given a JSON array of transactions, return a JSON object with a single "
        "'categories' field: a JSON array of the same length where each element is "
        "one of: Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, "
        "Entertainment, Other.\n\n"
        f"Transactions:\n{json.dumps(rows, ensure_ascii=False)}"
    )
    raw = _call_gemini(prompt, json_mode=True)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict) or "categories" not in parsed:
        return {"categories": ["Other"] * len(rows), "raw": raw}
    cats = parsed["categories"]
    if not isinstance(cats, list) or len(cats) != len(rows):
        return {"categories": ["Other"] * len(rows), "raw": raw}
    return {"categories": [_coerce_category(c) for c in cats], "raw": raw}


def classify_categories(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Classify the categories for a list of uncategorised transactions.

    Returns a list (one per input row) of dicts with ``llm_category``,
    ``llm_raw_response``, and ``llm_failed``. Batched at
    ``LLM_BATCH_SIZE`` rows per Gemini call.
    """
    settings = get_settings()
    batch_size = max(1, settings.llm_batch_size)
    out: list[dict[str, Any]] = []
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        result = _classify_call(batch)
        if result.get("llm_failed"):
            for _ in batch:
                out.append(
                    {
                        "llm_category": None,
                        "llm_raw_response": None,
                        "llm_failed": True,
                    }
                )
            continue
        cats = result.get("categories", ["Other"] * len(batch))
        for cat in cats:
            out.append(
                {
                    "llm_category": cat,
                    "llm_raw_response": result.get("raw"),
                    "llm_failed": False,
                }
            )
    return out


@retry_llm
def _summarize_call(payload: dict[str, Any]) -> dict[str, Any]:
    """Single call to Gemini for the narrative + risk_level + top merchants."""
    prompt = (
        "You are a financial analyst. Given a JSON object with totals, top merchants, "
        "and an anomaly list, return a JSON object with these exact fields:\n"
        "- 'total_spend_by_currency' (object: {currency_code: total})\n"
        "- 'top_3_merchants' (array of {merchant, total_inr})\n"
        "- 'anomaly_count' (int)\n"
        "- 'narrative' (2-3 sentences, plain text)\n"
        "- 'risk_level' (one of low, medium, high)\n\n"
        f"Input:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    raw = _call_gemini(prompt, json_mode=True)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        return {"raw": raw}
    return {**parsed, "raw": raw}


def generate_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Produce the narrative + risk_level + top_3_merchants for a job.

    On LLM failure returns ``{"llm_failed": True}``; the worker treats
    that as "use safe defaults and continue".
    """
    return _summarize_call(payload)
