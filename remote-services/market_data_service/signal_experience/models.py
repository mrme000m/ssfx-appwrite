"""Data models for the signal experience system."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ssfx_parser import TradeSignal


class Outcome:
    """Lifecycle outcome labels."""

    TP = "TP"
    SL = "SL"
    BE = "BE"
    CLOSE_PROFIT = "CLOSE_PROFIT"
    CLOSE_LOSS = "CLOSE_LOSS"
    CLOSE_UNKNOWN = "CLOSE_UNKNOWN"
    DELETED_PENDING = "DELETED_PENDING"
    OPEN = "OPEN"


@dataclass
class ExperienceAuthor:
    author: str
    total_signals: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_profit_pips: float | None = None
    avg_loss_pips: float | None = None
    profit_factor: float | None = None
    expectancy: float | None = None
    current_streak: int = 0
    max_drawdown_signals: int = 0
    avg_rr: float | None = None
    last_signal_at: str | None = None
    updated_at: str | None = None

    def to_appwrite(self) -> dict[str, Any]:
        return {
            "author": self.author,
            "total_signals": self.total_signals,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": round(self.win_rate, 4),
            "avg_profit_pips": self.avg_profit_pips,
            "avg_loss_pips": self.avg_loss_pips,
            "profit_factor": self.profit_factor,
            "expectancy": self.expectancy,
            "current_streak": self.current_streak,
            "max_drawdown_signals": self.max_drawdown_signals,
            "avg_rr": self.avg_rr,
            "last_signal_at": self.last_signal_at,
            "updated_at": self.updated_at or _iso_now(),
        }


@dataclass
class ExperienceSession:
    hour_utc: int
    total_signals: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_rr: float | None = None
    avg_time_to_update_min: float | None = None
    noise_ratio: float | None = None
    updated_at: str | None = None

    def to_appwrite(self) -> dict[str, Any]:
        return {
            "hour_utc": self.hour_utc,
            "total_signals": self.total_signals,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": round(self.win_rate, 4),
            "avg_rr": self.avg_rr,
            "avg_time_to_update_min": self.avg_time_to_update_min,
            "noise_ratio": self.noise_ratio,
            "updated_at": self.updated_at or _iso_now(),
        }


@dataclass
class ExperiencePattern:
    pattern_key: str
    symbol: str
    direction: str
    order_type: str | None = None
    total_signals: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_sl_pips: float | None = None
    avg_tp_pips: float | None = None
    expectancy: float | None = None
    confidence_score: float | None = None
    updated_at: str | None = None

    def to_appwrite(self) -> dict[str, Any]:
        return {
            "pattern_key": self.pattern_key,
            "symbol": self.symbol,
            "direction": self.direction,
            "order_type": self.order_type or "",
            "total_signals": self.total_signals,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": round(self.win_rate, 4),
            "avg_sl_pips": self.avg_sl_pips,
            "avg_tp_pips": self.avg_tp_pips,
            "expectancy": self.expectancy,
            "confidence_score": self.confidence_score,
            "updated_at": self.updated_at or _iso_now(),
        }


@dataclass
class ExperienceOverall:
    rolling_30d_win_rate: float | None = None
    signals_today: int | None = None
    good_vs_bad_ratio: float | None = None
    last_signal_id: int | None = None
    insights_json: str | None = None
    updated_at: str | None = None

    def to_appwrite(self) -> dict[str, Any]:
        return {
            "rolling_30d_win_rate": self.rolling_30d_win_rate,
            "signals_today": self.signals_today,
            "good_vs_bad_ratio": self.good_vs_bad_ratio,
            "last_signal_id": self.last_signal_id,
            "insights_json": self.insights_json,
            "updated_at": self.updated_at or _iso_now(),
        }


@dataclass
class SignalQualityLog:
    message_id: int
    chat_id: str | None = None
    raw_text: str | None = None
    author: str | None = None
    quality_score: float | None = None
    factors_json: str | None = None
    decision: str | None = None
    outcome: str | None = None
    outcome_pips: float | None = None
    closed_at: str | None = None
    updated_at: str | None = None

    def to_appwrite(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "chat_id": self.chat_id,
            "raw_text": self.raw_text,
            "author": self.author,
            "quality_score": self.quality_score,
            "factors_json": self.factors_json,
            "decision": self.decision,
            "outcome": self.outcome,
            "outcome_pips": self.outcome_pips,
            "closed_at": self.closed_at,
            "updated_at": self.updated_at or _iso_now(),
        }


@dataclass
class QualityFactors:
    """Inputs that determined a signal's quality score."""

    base_confidence: float
    author: str | None
    author_win_rate: float | None
    author_signals: int
    session_win_rate: float | None
    session_signals: int
    pattern_win_rate: float | None
    pattern_signals: int
    quality_penalty: float
    recency_boost: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_confidence": self.base_confidence,
            "author": self.author,
            "author_win_rate": self.author_win_rate,
            "author_signals": self.author_signals,
            "session_win_rate": self.session_win_rate,
            "session_signals": self.session_signals,
            "pattern_win_rate": self.pattern_win_rate,
            "pattern_signals": self.pattern_signals,
            "quality_penalty": self.quality_penalty,
            "recency_boost": self.recency_boost,
            "reasons": self.reasons,
        }


@dataclass
class ClassifiedMessage:
    """A raw message plus its extracted signal and category."""

    message_id: int
    date: str
    text: str
    reply_to_message_id: int | None
    author: str | None
    category: str
    signal: TradeSignal | None = None
    noise_reason: str | None = None
    quality_flags: list[str] = field(default_factory=list)

    @property
    def is_new_trade(self) -> bool:
        return self.category in ("new_trade_market", "new_trade_pending")

    @property
    def is_trade_update(self) -> bool:
        return self.category in (
            "close",
            "move_sl",
            "tp_hit",
            "sl_hit",
            "risk_hit",
            "cancel",
            "running",
        )


@dataclass
class LifecycleChain:
    """A trade entry and all its follow-up messages."""

    entry_message_id: int
    author: str | None
    symbol: str | None
    direction: str | None
    order_type: str | None
    entry_date: str
    messages: list[ClassifiedMessage] = field(default_factory=list)
    outcome: str = Outcome.OPEN
    outcome_pips: float | None = None
    first_update_min: float | None = None
    last_update_min: float | None = None
    closed_at: str | None = None
    quality_flags: list[str] = field(default_factory=list)


# Configurable thresholds (can be overridden by Appwrite service_config row)
DEFAULT_BLOCK_THRESHOLD = 0.50
DEFAULT_REDUCE_THRESHOLD = 0.75
MIN_SAMPLES_FOR_WIN_RATE = 10


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()
