"""SQLAlchemy 2.x database setup.

Single source of truth for the engine and session factory. Tests and
production both go through :func:`get_engine` / :func:`get_session`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

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


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a DB session and ensures cleanup."""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


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


def serialize_row(row: Any) -> dict[str, Any]:
    """Convert an ORM ``Transaction`` to the dict shape the API expects."""
    return {
        "txn_id": row.txn_id,
        "date": row.date.isoformat(),
        "merchant": row.merchant,
        "amount_original": row.amount_original,
        "currency_original": row.currency_original,
        "amount_inr": row.amount_inr,
        "status": row.status or "",
        "category": row.category or "",
        "account_id": row.account_id,
        "notes": row.notes or "",
        "is_suspicious": row.is_suspicious,
    }
