"""FastAPI dependency-injection helpers.

Routes ask for a :class:`JobStore` via :func:`get_job_store`; the
main app wires up a concrete implementation in :mod:`app.main`.
"""

from __future__ import annotations

from app.storage import JobStore

# Module-level reference; :func:`set_job_store` rebinds it during app startup.
_store: JobStore | None = None


def set_job_store(store: JobStore) -> None:
    """Register the active store (called from app lifespan and tests)."""
    global _store
    _store = store


def reset_job_store() -> None:
    """Clear the active store (used by tests to reset between cases)."""
    global _store
    _store = None


def get_job_store() -> JobStore:
    """Return the currently-registered JobStore.

    Raises a clear error if no store has been registered — happens when
    routes are exercised outside the FastAPI app context.
    """
    if _store is None:
        raise RuntimeError(
            "JobStore has not been initialized. Did you forget to call set_job_store() in app startup?"
        )
    return _store
