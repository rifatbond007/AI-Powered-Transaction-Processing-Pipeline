"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.config import get_settings
from app.dependencies import set_store
from app.etl import run_etl
from app.routes import health, summary, transactions
from app.store import InMemoryTransactionStore
from app.store_sql import SqlTransactionStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the data store on startup, clean up on shutdown.

    Storage backend is chosen via the ``APP_ENV`` env var (or explicitly by
    setting ``USE_IN_MEMORY_STORE=1``). Default is the SQL store.
    """
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    logger.info("Starting app (env=%s)", settings.app_env)

    use_in_memory = os.getenv("USE_IN_MEMORY_STORE") == "1"
    if use_in_memory:
        logger.info("Using in-memory store (USE_IN_MEMORY_STORE=1)")
        store = _build_in_memory_store(settings.csv_path)
    else:
        try:
            from sqlalchemy import inspect

            from app.database import get_engine, get_session_factory
            from app.models import Base, Transaction

            engine = get_engine()
            inspector = inspect(engine)
            if not inspector.has_table(Transaction.__tablename__):
                logger.info("Transactions table missing — creating schema")
                Base.metadata.create_all(bind=engine)
            store = SqlTransactionStore(get_session_factory())
            count = store.count()
            logger.info("SQL store ready (%d existing rows)", count)
        except Exception as e:
            logger.warning("SQL store unavailable (%s) — falling back to in-memory store", e)
            store = _build_in_memory_store(settings.csv_path)

    set_store(store)
    try:
        yield
    finally:
        logger.info("Shutting down app")


def _build_in_memory_store(csv_path: str) -> InMemoryTransactionStore:
    """Load the CSV via ETL and return an in-memory store populated with it."""
    path = Path(csv_path)
    store = InMemoryTransactionStore()
    if path.exists():
        result = run_etl(path)
        store.insert_many(result.clean_df.to_dict(orient="records"))
        logger.info(
            "In-memory store: %d clean rows, %d quarantined",
            len(result.clean_df),
            len(result.quarantine),
        )
    else:
        logger.warning("CSV %s not found — in-memory store is empty", path)
    return store


def create_app() -> FastAPI:
    """Application factory — used by tests and by the ASGI server."""
    app = FastAPI(
        title="AI-Powered Transaction Processing Pipeline",
        description=(
            "Ingests a messy transactions CSV, normalizes it, and serves it "
            "through a REST API. See README.md and instruction.md."
        ),
        version="0.2.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(transactions.router)
    app.include_router(summary.router)
    return app


app = create_app()
