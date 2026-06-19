"""RQ queue helpers.

The :class:`Queue` is constructed from a Redis connection built from
``settings.redis_url``. Tests override the underlying Redis client via
``monkeypatch.setattr("app.queue._redis", FakeRedis(...))``.
"""

from __future__ import annotations

import logging
from typing import Any

import redis
from rq import Queue

from app.config import get_settings

logger = logging.getLogger(__name__)

_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """Return a lazily-initialized Redis connection."""
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def get_queue(name: str | None = None) -> Queue:
    """Return the RQ :class:`Queue` for ``name`` (default: settings.rq_queue_name)."""
    settings = get_settings()
    return Queue(name or settings.rq_queue_name, connection=get_redis())


def enqueue_process_job(job_id: str, csv_path: str) -> Any:
    """Enqueue the :func:`app.worker.process_job` task. Returns the RQ Job."""
    from app.services.worker import process_job  # late import — avoids worker boot loop

    queue = get_queue()
    rq_job = queue.enqueue(process_job, job_id, csv_path)
    logger.info("Enqueued job %s as RQ %s", job_id, rq_job.id)
    return rq_job



