"""Incrementally update signal experience rows from observed outcomes."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from ssfx_parser import TradeSignal

from .models import (
    ExperienceAuthor,
    ExperienceOverall,
    ExperiencePattern,
    ExperienceSession,
    LifecycleChain,
    Outcome,
    SignalQualityLog,
)
from .store import SignalExperienceStore

logger = logging.getLogger(__name__)


class SignalExperienceUpdater:
    """Update experience tables when signal outcomes are resolved."""

    def __init__(self, store: SignalExperienceStore) -> None:
        self.store = store

    def update_from_chain(self, chain: LifecycleChain) -> None:
        """Update all experience dimensions from a resolved lifecycle chain."""
        if not chain.messages:
            return
        entry = chain.messages[0]
        if not entry.is_new_trade:
            return

        author_name = chain.author or "unknown"
        hour = _hour_utc_from_date(entry.date)
        pattern_key = _pattern_key(entry.signal) if entry.signal else "UNKNOWN_UNKNOWN_MARKET"
        outcome = chain.outcome
        pips = chain.outcome_pips

        self._update_author(author_name, outcome, pips, entry.date)
        self._update_session(hour, outcome, chain)
        self._update_pattern(pattern_key, entry, outcome, pips)
        self._update_overall(chain)
        self._update_quality_log(entry, outcome, pips, chain.closed_at)

    def update_from_outcome(
        self,
        signal: TradeSignal,
        author_name: str | None,
        outcome: str,
        outcome_pips: float | None = None,
        closed_at: str | None = None,
    ) -> None:
        """Update experience from a runtime outcome event."""
        hour = datetime.fromtimestamp(signal.timestamp_ms / 1000, tz=UTC).hour if signal.timestamp_ms else datetime.now(UTC).hour
        pattern_key = _pattern_key(signal)

        self._update_author(author_name or "unknown", outcome, outcome_pips, _iso_now())
        self._update_session_from_outcome(hour, outcome)
        self._update_pattern_from_signal(signal, pattern_key, outcome, outcome_pips)
        self._update_overall_from_outcome(outcome)
        self._update_quality_log_from_signal(signal, author_name, outcome, outcome_pips, closed_at)

    # ── Author updates ─────────────────────────────────────────────────────────

    def _update_author(self, author: str, outcome: str, pips: float | None, signal_date: str) -> None:
        row = self.store.get_author(author) or ExperienceAuthor(author=author)
        row.total_signals += 1
        row.last_signal_at = signal_date

        if outcome in (Outcome.TP, Outcome.CLOSE_PROFIT):
            row.win_count += 1
            row.current_streak = max(row.current_streak, 0) + 1
            if pips is not None:
                row.avg_profit_pips = _rolling_avg(row.avg_profit_pips, pips, row.win_count)
        elif outcome in (Outcome.SL, Outcome.CLOSE_LOSS):
            row.loss_count += 1
            row.current_streak = min(row.current_streak, 0) - 1
            row.max_drawdown_signals = max(row.max_drawdown_signals, abs(row.current_streak))
            if pips is not None:
                row.avg_loss_pips = _rolling_avg(row.avg_loss_pips, abs(pips), row.loss_count)
        else:
            # BE, OPEN, CLOSE_UNKNOWN, DELETED_PENDING: streak reset
            row.current_streak = 0

        row.win_rate = row.win_count / max(1, row.win_count + row.loss_count)
        row.avg_rr = _compute_avg_rr(row.avg_profit_pips, row.avg_loss_pips)
        row.profit_factor = _profit_factor(
            row.avg_profit_pips, row.win_count, row.avg_loss_pips, row.loss_count
        )
        row.expectancy = _expectancy(
            row.win_rate, row.avg_profit_pips, row.avg_loss_pips
        )
        row.updated_at = _iso_now()
        self.store.save_author(row)

    # ── Session updates ────────────────────────────────────────────────────────

    def _update_session(self, hour: int, outcome: str, chain: LifecycleChain) -> None:
        row = self.store.get_session(hour) or ExperienceSession(hour_utc=hour)
        self._apply_outcome_to_session(row, outcome)
        if chain.first_update_min is not None:
            row.avg_time_to_update_min = _rolling_avg(
                row.avg_time_to_update_min, chain.first_update_min, row.total_signals
            )
        row.updated_at = _iso_now()
        self.store.save_session(row)

    def _update_session_from_outcome(self, hour: int, outcome: str) -> None:
        row = self.store.get_session(hour) or ExperienceSession(hour_utc=hour)
        self._apply_outcome_to_session(row, outcome)
        row.updated_at = _iso_now()
        self.store.save_session(row)

    def _apply_outcome_to_session(self, row: ExperienceSession, outcome: str) -> None:
        row.total_signals += 1
        if outcome in (Outcome.TP, Outcome.CLOSE_PROFIT):
            row.win_count += 1
        elif outcome in (Outcome.SL, Outcome.CLOSE_LOSS):
            row.loss_count += 1
        row.win_rate = row.win_count / max(1, row.win_count + row.loss_count)

    # ── Pattern updates ────────────────────────────────────────────────────────

    def _update_pattern(self, pattern_key: str, entry, outcome: str, pips: float | None) -> None:
        row = self.store.get_pattern(pattern_key)
        if row is None and entry.signal:
            row = ExperiencePattern(
                pattern_key=pattern_key,
                symbol=entry.signal.symbol or "UNKNOWN",
                direction=entry.signal.direction.value if entry.signal.direction else "UNKNOWN",
                order_type=entry.signal.order_type.value if entry.signal.order_type else None,
            )
        if row is None:
            return
        self._apply_outcome_to_pattern(row, outcome, pips)
        row.updated_at = _iso_now()
        self.store.save_pattern(row)

    def _update_pattern_from_signal(
        self,
        signal: TradeSignal,
        pattern_key: str,
        outcome: str,
        pips: float | None,
    ) -> None:
        row = self.store.get_pattern(pattern_key)
        if row is None:
            row = ExperiencePattern(
                pattern_key=pattern_key,
                symbol=signal.symbol or "UNKNOWN",
                direction=signal.direction.value if signal.direction else "UNKNOWN",
                order_type=signal.order_type.value if signal.order_type else None,
            )
        self._apply_outcome_to_pattern(row, outcome, pips)
        row.updated_at = _iso_now()
        self.store.save_pattern(row)

    def _apply_outcome_to_pattern(
        self,
        row: ExperiencePattern,
        outcome: str,
        pips: float | None,
    ) -> None:
        row.total_signals += 1
        if outcome in (Outcome.TP, Outcome.CLOSE_PROFIT):
            row.win_count += 1
        elif outcome in (Outcome.SL, Outcome.CLOSE_LOSS):
            row.loss_count += 1
        row.win_rate = row.win_count / max(1, row.win_count + row.loss_count)
        if pips is not None:
            if pips >= 0:
                row.avg_tp_pips = _rolling_avg(row.avg_tp_pips, pips, row.win_count)
            else:
                row.avg_sl_pips = _rolling_avg(row.avg_sl_pips, abs(pips), row.loss_count)
        row.expectancy = _expectancy(row.win_rate, row.avg_tp_pips, row.avg_sl_pips)
        row.confidence_score = row.win_rate * min(1.0, row.total_signals / 30.0)

    # ── Overall updates ────────────────────────────────────────────────────────

    def _update_overall(self, chain: LifecycleChain) -> None:
        row = self.store.get_overall("global") or ExperienceOverall()
        row.signals_today = (row.signals_today or 0) + 1
        row.last_signal_id = chain.entry_message_id
        row.updated_at = _iso_now()
        self.store.save_overall(row)

    def _update_overall_from_outcome(self, outcome: str) -> None:
        row = self.store.get_overall("global") or ExperienceOverall()
        row.signals_today = (row.signals_today or 0) + 1
        row.updated_at = _iso_now()
        self.store.save_overall(row)

    # ── Quality log updates ────────────────────────────────────────────────────

    def _update_quality_log(
        self,
        entry,
        outcome: str,
        pips: float | None,
        closed_at: str | None,
    ) -> None:
        row = self.store.get_quality_log(entry.message_id)
        if row is None:
            row = SignalQualityLog(
                message_id=entry.message_id,
                chat_id=entry.signal.chat_id if entry.signal else None,
                raw_text=entry.text,
                author=entry.author,
            )
        row.outcome = outcome
        row.outcome_pips = pips
        row.closed_at = closed_at
        row.updated_at = _iso_now()
        self.store.save_quality_log(row)

    def _update_quality_log_from_signal(
        self,
        signal: TradeSignal,
        author_name: str | None,
        outcome: str,
        pips: float | None,
        closed_at: str | None,
    ) -> None:
        row = self.store.get_quality_log(signal.message_id) or SignalQualityLog(
            message_id=signal.message_id,
            chat_id=signal.chat_id,
            raw_text=signal.raw_text,
            author=author_name,
            quality_score=signal.quality_score,
            factors_json=json.dumps(signal.quality_factors) if signal.quality_factors else None,
            decision=signal.experience_action,
        )
        row.outcome = outcome
        row.outcome_pips = pips
        row.closed_at = closed_at
        row.updated_at = _iso_now()
        self.store.save_quality_log(row)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _rolling_avg(current: float | None, new_value: float, count: int) -> float:
    if current is None:
        return new_value
    return (current * (count - 1) + new_value) / count


def _compute_avg_rr(avg_profit: float | None, avg_loss: float | None) -> float | None:
    if not avg_profit or not avg_loss or avg_loss == 0:
        return None
    return avg_profit / avg_loss


def _profit_factor(
    avg_profit: float | None,
    win_count: int,
    avg_loss: float | None,
    loss_count: int,
) -> float | None:
    if not avg_profit or not avg_loss or loss_count == 0:
        return None
    gross_profit = avg_profit * win_count
    gross_loss = avg_loss * loss_count
    if gross_loss == 0:
        return None
    return gross_profit / gross_loss


def _expectancy(
    win_rate: float,
    avg_profit: float | None,
    avg_loss: float | None,
) -> float | None:
    if avg_profit is None or avg_loss is None:
        return None
    loss_rate = 1.0 - win_rate
    return (win_rate * avg_profit) - (loss_rate * avg_loss)


def _hour_utc_from_date(date_str: str) -> int:
    from datetime import datetime

    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    return dt.hour


def _pattern_key(signal: TradeSignal) -> str:
    symbol = signal.symbol or "UNKNOWN"
    direction = signal.direction.value if signal.direction else "UNKNOWN"
    order_type = signal.order_type.value if signal.order_type else "MARKET"
    return f"{symbol}_{direction}_{order_type}"


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()
