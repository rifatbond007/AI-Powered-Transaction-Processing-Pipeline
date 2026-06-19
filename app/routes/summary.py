"""Route for the /summary aggregate endpoint (Redis-cached)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app import cache
from app.config import Settings, get_settings
from app.dependencies import get_store
from app.schemas import Summary
from app.store import TransactionStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["summary"])

CACHE_KEY = "summary:v1"


@router.get("/summary", response_model=Summary)
def get_summary(
    store: TransactionStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> Summary:
    """Return aggregate summary statistics.

    Cached in Redis for ``SUMMARY_CACHE_TTL`` seconds. Cache failures
    degrade gracefully — we log and fall through to the DB.
    """
    cached = cache.cache_get_json(CACHE_KEY)
    if cached is not None:
        return Summary(**cached)

    raw = store.compute_summary()
    summary = Summary(**raw)
    cache.cache_set_json(CACHE_KEY, summary.model_dump(), settings.summary_cache_ttl_seconds)
    return summary


def clear_summary_cache() -> None:
    """Helper used by tests to reset the in-process cache between runs."""
    # No-op for Redis-backed cache — the real Redis instance is shared.
    # Tests should use a fresh Redis (fakeredis) or unique key prefixes.
    pass
