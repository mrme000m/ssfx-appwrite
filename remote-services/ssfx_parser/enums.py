"""Shared enums for trading configuration and signal handling."""
from __future__ import annotations

from enum import Enum


class SignalType(str, Enum):
    NEW = "NEW"
    TP_HIT = "TP_HIT"
    SL_HIT = "SL_HIT"
    CLOSE_HALF = "CLOSE_HALF"
    CLOSE_PARTIAL = "CLOSE_PARTIAL"
    CLOSE = "CLOSE"
    SL_TO_ENTRY = "SL_TO_ENTRY"
    CANCEL = "CANCEL"
    RUNNING = "RUNNING"
    RISK_HIT = "RISK_HIT"
    ENTRY_UPDATE = "ENTRY_UPDATE"
    IGNORE = "IGNORE"


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class SignalStatus(str, Enum):
    PENDING = "pending"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CLOSED = "closed"
    EMITTED = "emitted"


class FollowerExecutionStatus(str, Enum):
    PENDING = "pending"
    EXECUTING = "executing"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"
    CLOSED = "closed"
    SKIPPED = "skipped"


class VolumeMode(str, Enum):
    FIXED_LOTS = "fixed_lots"
    PERCENT_OF_BALANCE = "percent_of_balance"
    PERCENT_OF_EQUITY = "percent_of_equity"
    FIXED_CURRENCY_RISK = "fixed_currency_risk"
    PERCENT_RISK = "percent_risk"


class OrderHandling(str, Enum):
    FOLLOW_SIGNAL = "follow_signal"
    MARKET_ONLY = "market_only"
    LIMIT_ONLY = "limit_only"


class TpStrategy(str, Enum):
    TP1_ONLY = "tp1_only"
    ALL_TPS = "all_tps"
    NO_TP = "no_tp"


class SlStrategy(str, Enum):
    FOLLOW_SIGNAL = "follow_signal"
    NO_SL = "no_sl"
    TRAILING_AT_BREAKEVEN = "trailing_at_breakeven"


class ExecutionMode(str, Enum):
    DEMO = "demo"
    LIVE = "live"


class SecondUpdateAction(str, Enum):
    FULL_CLOSE = "full_close"
    HALF_CLOSE = "half_close"
    IGNORE = "ignore"


class EntryUpdateAction(str, Enum):
    AMEND_PENDING = "amend_pending"
    IGNORE = "ignore"
    CLOSE_AND_REOPEN = "close_and_reopen"
