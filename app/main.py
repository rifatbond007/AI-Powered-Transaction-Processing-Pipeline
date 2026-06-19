"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.config import get_settings
from app.dependencies import set_store
from app.etl import run_etl
from app.routes import health, summary, transactions
from app.store import InMemoryTransactionStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the data store on startup, clean up on shutdown."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    logger.info("Starting app (env=%s)", settings.app_env)

    csv_path = Path(settings.csv_path)
    if csv_path.exists():
        logger.info("Loading data from %s", csv_path)
        result = run_etl(csv_path)
        store = InMemoryTransactionStore()
        # Convert each DataFrame row to a JSON-friendly dict before storing.
        store.insert_many(result.clean_df.to_dict(orient="records"))
        logger.info(
            "Loaded %d clean rows, %d quarantined",
            len(result.clean_df),
            len(result.quarantine),
        )
    else:
        logger.warning("CSV %s not found — starting with empty store", csv_path)
        store = InMemoryTransactionStore()

    set_store(store)
    try:
        yield
    finally:
        logger.info("Shutting down app")


def create_app() -> FastAPI:
    """Application factory — used by tests and by the ASGI server."""
    app = FastAPI(
        title="AI-Powered Transaction Processing Pipeline",
        description=(
            "Ingests a messy transactions CSV, normalizes it, and serves it "
            "through a REST API. See README.md and instruction.md."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(transactions.router)
    app.include_router(summary.router)
    return app


app = create_app()
