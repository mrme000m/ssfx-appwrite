"""SSFX signal parser — reusable, framework-free parsing layer."""
from __future__ import annotations

from .enums import (
    Direction,
    EntryUpdateAction,
    ExecutionMode,
    ExecutionStatus,
    OrderHandling,
    OrderType,
    SecondUpdateAction,
    SignalStatus,
    SignalType,
    SlStrategy,
    TpStrategy,
    VolumeMode,
)
from .models import RawMessage, SlaveExecution, TradeSignal
from .parser import parse_signal
from .parsers import AgentConfig, ChainedParser, LlmSignalParser, RegexSignalParser, SignalParser

__all__ = [
    "AgentConfig",
    "ChainedParser",
    "Direction",
    "EntryUpdateAction",
    "ExecutionMode",
    "ExecutionStatus",
    "SlaveExecution",
    "LlmSignalParser",
    "OrderHandling",
    "OrderType",
    "RawMessage",
    "RegexSignalParser",
    "SecondUpdateAction",
    "SignalParser",
    "SignalStatus",
    "SignalType",
    "SlStrategy",
    "TpStrategy",
    "TradeSignal",
    "VolumeMode",
    "parse_signal",
]
