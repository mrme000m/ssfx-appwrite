"""Factory for creating signal experience stores."""
from __future__ import annotations

import logging
import os
from typing import Literal

from .base_store import BaseSignalExperienceStore
from .store import SignalExperienceStore
from .sqlite_store import SQLiteSignalExperienceStore

logger = logging.getLogger(__name__)


def create_signal_experience_store(
    backend: Literal["appwrite", "sqlite", "auto"] = "auto",
    **kwargs,
) -> BaseSignalExperienceStore:
    """
    Create a signal experience store based on configuration.
    
    Args:
        backend: "appwrite", "sqlite", or "auto" (default)
        **kwargs: Additional arguments passed to the store constructor
    
    Returns:
        A signal experience store instance
    
    Raises:
        RuntimeError: If backend is "appwrite" but Appwrite is unavailable
    """
    if backend == "auto":
        # Try Appwrite first, fall back to SQLite if unavailable
        try:
            store = SignalExperienceStore(**kwargs)
            # Test connection
            store.list_authors()
            logger.info("Using Appwrite signal experience store")
            return store
        except Exception as exc:
            logger.warning(
                "Appwrite signal experience store unavailable (%s); falling back to SQLite",
                exc,
            )
            backend = "sqlite"
    
    if backend == "appwrite":
        store = SignalExperienceStore(**kwargs)
        logger.info("Using Appwrite signal experience store")
        return store
    
    if backend == "sqlite":
        db_path = kwargs.get("db_path") or os.environ.get(
            "SIGNAL_EXPERIENCE_SQLITE_PATH",
            "/app/data/experience.db",
        )
        store = SQLiteSignalExperienceStore(db_path=db_path)
        logger.info("Using SQLite signal experience store at %s", db_path)
        return store
    
    raise ValueError(f"Unknown signal experience backend: {backend}")


# Backward compatibility
def SignalExperienceStoreFactory(
    database_id: str | None = None,
    endpoint: str | None = None,
    project_id: str | None = None,
    api_key: str | None = None,
    backend: Literal["appwrite", "sqlite", "auto"] = "auto",
    db_path: str | None = None,
) -> BaseSignalExperienceStore:
    """
    Factory function for backward compatibility.
    
    Args:
        database_id: Appwrite database ID (for Appwrite backend)
        endpoint: Appwrite endpoint (for Appwrite backend)
        project_id: Appwrite project ID (for Appwrite backend)
        api_key: Appwrite API key (for Appwrite backend)
        backend: Store backend ("appwrite", "sqlite", or "auto")
        db_path: SQLite database path (for SQLite backend)
    
    Returns:
        A signal experience store instance
    """
    kwargs = {}
    if backend in ["appwrite", "auto"]:
        kwargs.update({
            "database_id": database_id,
            "endpoint": endpoint,
            "project_id": project_id,
            "api_key": api_key,
        })
    if backend == "sqlite" and db_path:
        kwargs["db_path"] = db_path
    
    return create_signal_experience_store(backend=backend, **kwargs)