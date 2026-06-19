"""Route for the /summary aggregate endpoint (Redis-cached in Segment 4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.dependencies import get_store
from app.schemas import Summary
from app.store import TransactionStore

router = APIRouter(tags=["summary"])

# Simple in-process cache for now; Segment 4 swaps in Redis.
_cache: dict[str, Summary] = {}


@router.get("/summary", response_model=Summary)
def get_summary(
    store: TransactionStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> Summary:
    """Return aggregate summary statistics.

    Cached for ``SUMMARY_CACHE_TTL`` seconds. Cache failures are non-fatal
    (logged at WARNING, served from DB) — see instruction.md section 7.
    """
    cache_key = "summary:v1"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    raw = store.compute_summary()
    summary = Summary(**raw)
    _cache[cache_key] = summary
    return summary


def clear_summary_cache() -> None:
    """Helper used by tests to reset the in-process cache between runs."""
    _cache.clear()
