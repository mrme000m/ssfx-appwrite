"""Build a compact signal-intelligence context for LLM agents.

The context is assembled from two sources:
- the local/cloud signal store (recent raw/parsed messages)
- the signal experience store (author/session/pattern statistics)

It answers questions such as:
- Has this trader just burned through 2-3 stop-losses?
- Did they flip direction recently?
- Is this hour/pattern historically good or bad?
- What is the 30-day regime?
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ssfx_parser import TradeSignal

from .classifier import extract_author
from .models import ExperienceAuthor, Outcome
from .store import SignalExperienceStore


# Active signal statuses from the protocol contract
_ACTIVE_STATUSES = {"pending", "emitted", "executed"}


def _pattern_key(signal: TradeSignal) -> str:
    symbol = signal.symbol or "UNKNOWN"
    direction = signal.direction.value if signal.direction else "UNKNOWN"
    order_type = signal.order_type.value if signal.order_type else "MARKET"
    return f"{symbol}_{direction}_{order_type}"


def _utc_hour_now() -> int:
    return datetime.now(UTC).hour


def _recent_outcomes(author: str, store: SignalExperienceStore, limit: int = 5) -> list[dict[str, Any]]:
    logs = store.list_recent_logs_by_author(author, limit=limit * 2)
    closed = [log for log in logs if log.outcome is not None]
    result: list[dict[str, Any]] = []
    for log in closed[:limit]:
        result.append(
            {
                "message_id": log.message_id,
                "outcome": log.outcome,
                "outcome_pips": log.outcome_pips,
                "decision": log.decision,
                "raw_text": (log.raw_text or "")[:120],
            }
        )
    return result


def _opposite_direction_context(
    signal: TradeSignal,
    signal_store: Any,
    lookback_minutes: int = 60,
) -> dict[str, Any]:
    """Return details if the same author posted an opposite-direction signal recently."""
    if signal.symbol is None or signal.direction is None or signal.chat_id is None:
        return {"detected": False}

    author = extract_author(signal.raw_text)
    opposite = "SELL" if signal.direction.value == "BUY" else "BUY"
    cutoff_ms = int(datetime.now(UTC).timestamp() * 1000) - (lookback_minutes * 60 * 1000)

    # Collect candidate signals from today and active store rows.
    candidates: list[TradeSignal] = []
    try:
        candidates.extend(signal_store.get_today_messages(signal.chat_id))
    except Exception:
        try:
            candidates.extend(signal_store.get_active_signals(signal.chat_id))
        except Exception:
            candidates = []

    # get_today_messages returns RawMessages which don't have direction; skip those.
    typed_signals: list[TradeSignal] = []
    for candidate in candidates:
        if isinstance(candidate, TradeSignal):
            typed_signals.append(candidate)

    # If the store only gave raw messages, fall back to listing parsed signals.
    if not typed_signals:
        try:
            typed_signals = signal_store.list_signals(limit=200)
        except Exception:
            typed_signals = []

    for other in typed_signals:
        if other.message_id == signal.message_id:
            continue
        if other.chat_id != signal.chat_id:
            continue
        if (other.timestamp_ms or 0) < cutoff_ms:
            continue
        if other.symbol and other.symbol.upper() != signal.symbol.upper():
            continue
        if other.direction and other.direction.value == opposite:
            other_author = extract_author(other.raw_text)
            if author and other_author and other_author != author:
                continue
            return {
                "detected": True,
                "minutes_ago": round((signal.timestamp_ms or 0) - (other.timestamp_ms or 0)) / (60 * 1000),
                "opposite_message_id": other.message_id,
                "opposite_raw_text": other.raw_text[:120],
                "opposite_entry_price": other.entry_price,
            }

    return {"detected": False}


def _author_stats(author: str, store: SignalExperienceStore) -> dict[str, Any] | None:
    try:
        row = store.get_author(author)
        if row is None:
            return None
        return {
            "author": row.author,
            "total_signals": row.total_signals,
            "win_count": row.win_count,
            "loss_count": row.loss_count,
            "win_rate": row.win_rate,
            "avg_profit_pips": row.avg_profit_pips,
            "avg_loss_pips": row.avg_loss_pips,
            "profit_factor": row.profit_factor,
            "expectancy": row.expectancy,
            "current_streak": row.current_streak,
            "max_drawdown_signals": row.max_drawdown_signals,
            "avg_rr": row.avg_rr,
            "last_signal_at": row.last_signal_at,
        }
    except Exception:
        return None


def _pattern_stats(signal: TradeSignal, store: SignalExperienceStore) -> dict[str, Any] | None:
    try:
        row = store.get_pattern(_pattern_key(signal))
        if row is None:
            return None
        return {
            "pattern_key": row.pattern_key,
            "total_signals": row.total_signals,
            "win_rate": row.win_rate,
            "avg_sl_pips": row.avg_sl_pips,
            "avg_tp_pips": row.avg_tp_pips,
            "expectancy": row.expectancy,
            "confidence_score": row.confidence_score,
        }
    except Exception:
        return None


def _session_stats(store: SignalExperienceStore) -> dict[str, Any] | None:
    try:
        row = store.get_session(_utc_hour_now())
        if row is None:
            return None
        return {
            "hour_utc": row.hour_utc,
            "total_signals": row.total_signals,
            "win_rate": row.win_rate,
            "avg_rr": row.avg_rr,
        }
    except Exception:
        return None


def _overall_stats(store: SignalExperienceStore) -> dict[str, Any] | None:
    try:
        row = store.get_overall("global")
        if row is None:
            return None
        return {
            "rolling_30d_win_rate": row.rolling_30d_win_rate,
            "signals_today": row.signals_today,
            "good_vs_bad_ratio": row.good_vs_bad_ratio,
            "insights_json": row.insights_json,
        }
    except Exception:
        return None


def build_entry_context(
    signal: TradeSignal,
    signal_store: Any,
    experience_store: SignalExperienceStore,
    *,
    lookback_minutes: int = 60,
    recent_outcomes_limit: int = 5,
) -> dict[str, Any]:
    """Return a JSON-serializable intelligence block for the entry-decision agent."""
    author = extract_author(signal.raw_text)
    
    # Initialize with defaults
    context = {
        "author": author,
        "author_stats": None,
        "recent_outcomes": [],
        "opposite_direction_recent": {"detected": False},
        "pattern_stats": None,
        "session_stats": None,
        "overall_stats": None,
        "quality_score": signal.quality_score,
        "quality_factors": signal.quality_factors,
        "experience_action": signal.experience_action,
    }
    
    try:
        # Try to populate from stores
        if author:
            context["author_stats"] = _author_stats(author, experience_store)
            context["recent_outcomes"] = _recent_outcomes(author, experience_store, recent_outcomes_limit)
        
        context["opposite_direction_recent"] = _opposite_direction_context(
            signal, signal_store, lookback_minutes=lookback_minutes
        )
        context["pattern_stats"] = _pattern_stats(signal, experience_store)
        context["session_stats"] = _session_stats(experience_store)
        context["overall_stats"] = _overall_stats(experience_store)
        
    except Exception as exc:
        # Log but don't fail - return partial context
        import logging
        logger = logging.getLogger(__name__)
        logger.debug("Failed to build complete entry context: %s", exc)
    
    return context


def build_intent_context(
    signal: TradeSignal,
    signal_store: Any,
    experience_store: SignalExperienceStore,
) -> dict[str, Any]:
    """Return a lightweight intelligence block for the signal-intent classifier."""
    author = extract_author(signal.raw_text)
    
    # Initialize with defaults
    ctx: dict[str, Any] = {
        "author": author,
        "author_stats": None,
        "opposite_direction_recent": {"detected": False},
        "one_line_summary": f"No prior statistics for author {author}." if author else "Author unknown.",
    }
    
    try:
        # Try to populate from stores
        if author:
            ctx["author_stats"] = _author_stats(author, experience_store)
        
        ctx["opposite_direction_recent"] = _opposite_direction_context(signal, signal_store, lookback_minutes=60)
        
        # Update summary based on available stats
        stats = ctx["author_stats"]
        if stats:
            ctx["one_line_summary"] = (
                f"Author {author}: streak={stats['current_streak']}, "
                f"win_rate={stats['win_rate']:.2f}, expectancy={stats['expectancy']}"
            )
        
    except Exception as exc:
        # Log but don't fail - return partial context
        import logging
        logger = logging.getLogger(__name__)
        logger.debug("Failed to build complete intent context: %s", exc)
    
    return ctx


def format_entry_context_for_prompt(context: dict[str, Any]) -> str:
    """Render the entry context as a markdown block suitable for an LLM prompt."""
    lines = ["## Signal Intelligence", ""]

    author_stats = context.get("author_stats")
    if author_stats:
        lines.append(
            f"- Author: {author_stats['author']} (total={author_stats['total_signals']}, "
            f"win_rate={author_stats['win_rate']:.2f}, expectancy="
            f"{author_stats['expectancy'] or 'n/a'} pips, "
            f"streak={author_stats['current_streak']})"
        )
    else:
        lines.append(f"- Author: {context.get('author') or 'unknown'} (no historical stats)")

    outcomes = context.get("recent_outcomes", [])
    if outcomes:
        summary = ", ".join(f"{o['outcome']}({o['outcome_pips']})" for o in outcomes)
        lines.append(f"- Last {len(outcomes)} closed signals for this author: {summary}")

    opp = context.get("opposite_direction_recent", {})
    if opp.get("detected"):
        lines.append(
            f"- ⚠️ Opposite-direction signal detected {opp.get('minutes_ago', '?')} min ago "
            f"(msg #{opp.get('opposite_message_id')}). Treat as a fresh signal."
        )

    pattern = context.get("pattern_stats")
    if pattern:
        lines.append(
            f"- Pattern {pattern['pattern_key']}: win_rate={pattern['win_rate']:.2f}, "
            f"expectancy={pattern['expectancy'] or 'n/a'}, n={pattern['total_signals']}"
        )

    session = context.get("session_stats")
    if session:
        lines.append(
            f"- UTC hour {session['hour_utc']}: win_rate={session['win_rate']:.2f}, "
            f"n={session['total_signals']}"
        )

    overall = context.get("overall_stats")
    if overall:
        lines.append(
            f"- Overall: 30d_win_rate={overall['rolling_30d_win_rate'] or 'n/a'}, "
            f"signals_today={overall['signals_today'] or 0}"
        )

    lines.append(f"- Quality score: {context.get('quality_score')} ({context.get('experience_action')})")
    lines.append("")
    return "\n".join(lines)
