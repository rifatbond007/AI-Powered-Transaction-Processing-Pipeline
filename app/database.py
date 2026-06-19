"""SQLAlchemy 2.x database setup.

Single source of truth for the engine and session factory. Tests and
production both go through :func:`get_engine` / :func:`get_session_factory`.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return a lazily-initialized SQLAlchemy engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,  # detect dead connections
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return a lazily-initialized session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False
        )
    return _SessionLocal


def reset_for_tests() -> None:
    """Drop the engine / factory singletons. Used by tests."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def create_all_tables() -> None:
    """Create all tables. Used by ``scripts/init_db.py`` and tests."""
    from app import models

    engine = get_engine()
    models.Base.metadata.create_all(bind=engine)
