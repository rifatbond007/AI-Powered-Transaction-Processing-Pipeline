"""Container entrypoint.

Waits for Postgres + Redis to be reachable, ensures the DB schema exists
(no ETL bootstrap — jobs are the source of truth), then execs the CMD
(default: uvicorn).
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
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
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


def _ensure_schema() -> None:
    """Create tables only if missing; never drop on boot."""
    from sqlalchemy import inspect

    from app.database import get_engine
    from app.models import Base

    engine = get_engine()
    inspector = inspect(engine)
    if not inspector.has_table("jobs"):
        logger.info("Schema missing — creating tables")
        Base.metadata.create_all(bind=engine)
    else:
        logger.info("Schema already present")


def _ensure_upload_dir() -> None:
    """Create the upload directory if it doesn't exist yet."""
    upload_dir = os.getenv("UPLOAD_DIR", "/tmp/uploads")
    Path(upload_dir).mkdir(parents=True, exist_ok=True)
    logger.info("Upload directory ready: %s", upload_dir)


def main() -> int:
    logger.info("Entrypoint starting (APP_ENV=%s)", os.getenv("APP_ENV", "development"))

    db_url = os.getenv(
        "DATABASE_URL", "postgresql+psycopg2://postgres:postgres@postgres:5432/transactions"
    )
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    db_host, db_port = _parse_host_port(db_url, 5432)
    redis_host, redis_port = _parse_host_port(redis_url, 6379)

    _wait_for_tcp(db_host, db_port, label="postgres")
    _wait_for_tcp(redis_host, redis_port, label="redis")

    _ensure_upload_dir()

    try:
        _ensure_schema()
    except Exception as e:
        logger.error("DB init failed: %s", e)

    # Use docker-compose command override if provided, else CMD env var, else default.
    if len(sys.argv) > 1:
        cmd = " ".join(sys.argv[1:])
    else:
        cmd = os.getenv("CMD", "uvicorn app.main:app --host 0.0.0.0 --port 8000")
    logger.info("Starting: %s", cmd)
    os.execvp("sh", ["sh", "-c", cmd])
    return 0  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
