"""Execution backends for SSFX trade executor."""
from __future__ import annotations

from .base import ExecutionBackend
from .simulated import SimulatedBackend

__all__ = ["ExecutionBackend", "SimulatedBackend"]
