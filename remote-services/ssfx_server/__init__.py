"""SSFX webhook server — receives Telegram bot updates and routes to slaves."""
from __future__ import annotations

__all__ = ["app", "main"]


def __getattr__(name: str):
    if name in __all__:
        from .web_app import app, main
        return {"app": app, "main": main}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
