"""Container entrypoint.

Waits for Postgres and Redis to be reachable, ensures the DB schema exists,
loads CSV data on first boot, then execs the CMD (default: uvicorn).
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

# Allow `python scripts/entrypoint.py ...` to find the `app` package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s — %(message)s"
)
logger = logging.getLogger("entrypoint")


def _parse_host_port(url: str, default_port: int) -> tuple[str, int]:
    """Pull host/port out of a postgres:// or redis:// URL."""
    if "://" in url:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or default_port
        return host, port
    return "localhost", default_port


def _wait_for_tcp(host: str, port: int, *, timeout: int = 60, label: str = "service") -> None:
    """Block until ``host:port`` accepts TCP connections, or raise."""
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3):
                logger.info("%s is reachable at %s:%d", label, host, port)
                return
        except OSError as e:
            last_err = e
            time.sleep(1.0)
    raise RuntimeError(f"Timed out waiting for {label} at {host}:{port} ({last_err})")


def _init_database_if_empty() -> None:
    """Create tables and load CSV only on a fresh DB.

    Skips the (slow) ETL on restarts so the container starts fast.
    """
    from sqlalchemy import inspect

    from app.database import get_engine, get_session_factory
    from app.models import Base
    from app.store_sql import SqlTransactionStore

    engine = get_engine()
    inspector = inspect(engine)
    if not inspector.has_table("transactions"):
        logger.info("Schema missing — running init_db")
        Base.metadata.create_all(bind=engine)
        from scripts.init_db import main as run_init  # type: ignore[import-not-found]

        run_init()
        return

    factory = get_session_factory()
    store = SqlTransactionStore(factory)
    count = store.count()
    logger.info("Database already initialized (%d rows)", count)
    if count == 0:
        logger.info("Empty DB detected — running init_db")
        from scripts.init_db import main as run_init  # type: ignore[import-not-found]

        run_init()


def main() -> int:
    settings_env = os.getenv("APP_ENV", "development")
    logger.info("Entrypoint starting (APP_ENV=%s)", settings_env)

    # In docker-compose, service hostnames are 'postgres' and 'redis'.
    db_url = os.getenv(
        "DATABASE_URL", "postgresql+psycopg2://postgres:postgres@postgres:5432/transactions"
    )
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    db_host, db_port = _parse_host_port(db_url, 5432)
    redis_host, redis_port = _parse_host_port(redis_url, 6379)

    # Skip waiting when explicitly in-memory (e.g. local dev).
    if os.getenv("USE_IN_MEMORY_STORE") != "1":
        _wait_for_tcp(db_host, db_port, label="postgres")
        _wait_for_tcp(redis_host, redis_port, label="redis")
        try:
            _init_database_if_empty()
        except Exception as e:
            logger.error("DB init failed: %s — starting anyway (API will use fallback store)", e)

    # Exec the CMD — replaces this process so signals are forwarded correctly.
    cmd = os.getenv("CMD", "uvicorn app.main:app --host 0.0.0.0 --port 8000")
    logger.info("Starting: %s", cmd)
    os.execvp("sh", ["sh", "-c", cmd])
    return 0  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
