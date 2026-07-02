"""Compute real-time signal quality scores from experience tables."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from ssfx_parser import TradeSignal
from ssfx_parser.enums import SignalType

from .models import (
    DEFAULT_BLOCK_THRESHOLD,
    DEFAULT_REDUCE_THRESHOLD,
    MIN_SAMPLES_FOR_WIN_RATE,
    ExperienceAuthor,
    ExperiencePattern,
    ExperienceSession,
    QualityFactors,
)
from .store import SignalExperienceStore

logger = logging.getLogger(__name__)


def _pattern_key(signal: TradeSignal) -> str:
    symbol = signal.symbol or "UNKNOWN"
    direction = signal.direction.value if signal.direction else "UNKNOWN"
    order_type = signal.order_type.value if signal.order_type else "MARKET"
    return f"{symbol}_{direction}_{order_type}"


def _hour_utc_from_ms(timestamp_ms: int | None) -> int:
    if timestamp_ms is None:
        return datetime.now(UTC).hour
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).hour


def _quality_penalty(signal: TradeSignal) -> tuple[float, list[str]]:
    """Penalty factor for data-quality issues (1.0 = no penalty)."""
    penalty = 1.0
    reasons: list[str] = []
    if signal.signal_type != SignalType.NEW:
        return penalty, reasons
    if signal.sl is None:
        penalty *= 0.85
        reasons.append("missing_sl")
    if not signal.take_profits:
        penalty *= 0.85
        reasons.append("missing_tp")
    if signal.sl_float is not None and signal.entry_price is not None:
        if signal.direction.value == "BUY" and signal.sl_float >= signal.entry_price:
            penalty *= 0.5
            reasons.append("corrupted_sl")
        if signal.direction.value == "SELL" and signal.sl_float <= signal.entry_price:
            penalty *= 0.5
            reasons.append("corrupted_sl")
    for tp in signal.take_profits:
        if signal.entry_price is not None:
            if signal.direction.value == "BUY" and tp <= signal.entry_price:
                penalty *= 0.5
                reasons.append("corrupted_tp")
            if signal.direction.value == "SELL" and tp >= signal.entry_price:
                penalty *= 0.5
                reasons.append("corrupted_tp")
    return penalty, reasons


def _recency_boost(author: ExperienceAuthor | None) -> tuple[float, list[str]]:
    """Boost signals from authors/sessions with recent positive activity."""
    boost = 1.0
    reasons: list[str] = []
    if author is None:
        return boost, reasons
    if author.current_streak > 0:
        boost += min(author.current_streak * 0.05, 0.25)
        reasons.append(f"author_positive_streak:{author.current_streak}")
    elif author.current_streak < -2:
        boost -= min(abs(author.current_streak) * 0.05, 0.25)
        reasons.append(f"author_negative_streak:{author.current_streak}")
    return max(0.5, boost), reasons


class SignalExperienceScorer:
    """Score incoming signals using persisted experience tables."""

    def __init__(
        self,
        store: SignalExperienceStore,
        block_threshold: float = DEFAULT_BLOCK_THRESHOLD,
        reduce_threshold: float = DEFAULT_REDUCE_THRESHOLD,
    ) -> None:
        self.store = store
        self.block_threshold = block_threshold
        self.reduce_threshold = reduce_threshold

    def score(self, signal: TradeSignal, author_name: str | None = None) -> tuple[float, QualityFactors, str]:
        """Return (quality_score, factors, decision)."""
        base_confidence = max(0.1, min(1.0, signal.parse_confidence))
        hour = _hour_utc_from_ms(signal.timestamp_ms)
        pattern_key = _pattern_key(signal)

        author = self.store.get_author(author_name) if author_name else None
        session = self.store.get_session(hour)
        pattern = self.store.get_pattern(pattern_key)

        author_win_rate = author.win_rate if author else None
        author_signals = author.total_signals if author else 0
        session_win_rate = session.win_rate if session else None
        session_signals = session.total_signals if session else 0
        pattern_win_rate = pattern.win_rate if pattern else None
        pattern_signals = pattern.total_signals if pattern else 0

        score = base_confidence

        # Only apply win-rate modifiers when we have enough samples
        if author and author_signals >= MIN_SAMPLES_FOR_WIN_RATE:
            score *= (0.5 + author_win_rate)
        else:
            score *= 0.9

        if session and session_signals >= MIN_SAMPLES_FOR_WIN_RATE:
            score *= (0.5 + session_win_rate)
        else:
            score *= 1.0

        if pattern and pattern_signals >= MIN_SAMPLES_FOR_WIN_RATE:
            score *= (0.5 + pattern_win_rate)
        else:
            score *= 1.0

        quality_penalty, quality_reasons = _quality_penalty(signal)
        score *= quality_penalty

        recency_boost, recency_reasons = _recency_boost(author)
        score *= recency_boost

        score = max(0.0, min(1.0, score))

        factors = QualityFactors(
            base_confidence=base_confidence,
            author=author_name,
            author_win_rate=author_win_rate,
            author_signals=author_signals,
            session_win_rate=session_win_rate,
            session_signals=session_signals,
            pattern_win_rate=pattern_win_rate,
            pattern_signals=pattern_signals,
            quality_penalty=quality_penalty,
            recency_boost=recency_boost,
            reasons=quality_reasons + recency_reasons,
        )

        if score < self.block_threshold:
            decision = "block"
        elif score < self.reduce_threshold:
            decision = "reduce"
        else:
            decision = "allow"

        return score, factors, decision

    def enrich(self, signal: TradeSignal, author_name: str | None = None) -> TradeSignal:
        """Attach quality score and decision to a TradeSignal in-place."""
        score, factors, decision = self.score(signal, author_name)
        signal.quality_score = round(score, 4)
        signal.quality_factors = factors.to_dict()
        signal.experience_action = decision
        return signal
