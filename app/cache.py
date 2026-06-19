"""Redis cache wrapper.

A thin layer over :mod:`redis` that:
  - reads the URL from settings,
  - exposes a tiny ``get`` / ``set`` interface with JSON serialization,
  - degrades gracefully — cache failures are non-fatal (instruction 7).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: Redis | None = None


def get_redis() -> Redis:
    """Return a lazily-initialized Redis client."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


def cache_get_json(key: str) -> Any | None:
    """Fetch a JSON value from cache; return None on miss or any error."""
    try:
        raw = get_redis().get(key)
    except RedisError as e:
        logger.warning("Cache GET failed (key=%s): %s", key, e)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as e:
        logger.warning("Cache value at %s is not valid JSON: %s", key, e)
        return None


def cache_set_json(key: str, value: Any, ttl_seconds: int) -> bool:
    """Store a JSON value with a TTL. Returns False on any cache error."""
    try:
        get_redis().set(key, json.dumps(value), ex=ttl_seconds)
    except RedisError as e:
        logger.warning("Cache SET failed (key=%s): %s", key, e)
        return False
    return True


def reset_for_tests() -> None:
    """Drop the client singleton. Used by tests."""
    global _client
    _client = None
