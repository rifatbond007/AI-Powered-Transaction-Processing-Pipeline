"""FastAPI dependency-injection helpers.

Routes ask for a :class:`TransactionStore` via :func:`get_store`, and the
main app wires up a concrete implementation in :mod:`app.main`.
"""

from __future__ import annotations

from app.store import TransactionStore

# Module-level reference; :func:`set_store` rebinds it during app startup.
_store: TransactionStore | None = None


def set_store(store: TransactionStore) -> None:
    """Register the active store (called from app lifespan)."""
    global _store
    _store = store


def get_store() -> TransactionStore:
    """Return the currently-registered store.

    Raises a clear error if no store has been registered — happens when
    routes are exercised outside the FastAPI app context.
    """
    if _store is None:
        raise RuntimeError(
            "TransactionStore has not been initialized. "
            "Did you forget to call set_store() in app startup?"
        )
    return _store
