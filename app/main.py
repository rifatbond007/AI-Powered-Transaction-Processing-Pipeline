"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.dependencies import get_job_store, set_job_store
from app.routes import health, jobs
from app.adapters.storage import SqlJobStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the job store on startup, clean up on shutdown."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    logger.info("Starting app (env=%s)", settings.app_env)

    # Only create a store if one has not already been registered (tests set
    # their own store in fixtures; this avoids clobbering it on lifespan start).
    try:
        existing = get_job_store()
        logger.info("Reusing pre-registered JobStore: %s", type(existing).__name__)
    except RuntimeError:
        from app.database import get_engine, get_session_factory
        from app.models import Base

        engine = get_engine()
        Base.metadata.create_all(bind=engine)
        set_job_store(SqlJobStore(get_session_factory()))
        logger.info("Initialized SQL JobStore")

    try:
        yield
    finally:
        logger.info("Shutting down app")


def create_app() -> FastAPI:
    """Application factory — used by tests and by the ASGI server."""
    app = FastAPI(
        title="AI-Powered Transaction Processing Pipeline",
        description=(
            "Async job-based CSV ingestion. Upload a CSV -> /jobs/{id}/status -> /jobs/{id}/results."
        ),
        version="0.3.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(jobs.router)
    return app


app = create_app()
