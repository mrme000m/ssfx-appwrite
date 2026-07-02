"""Classify raw Telegram messages into signal categories."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ssfx_parser import SignalType, TradeSignal, parse_signal
from ssfx_parser.enums import OrderType

from .models import ClassifiedMessage

NOISE_KEYWORDS = [
    "copier",
    "70% off",
    "discount",
    "offer",
    "promo",
    "join now",
    "invalid parameters",
    "congratulations",
    "welcome",
]

AD_KEYWORDS = [
    "ssf copier",
    "copy my trades",
    "vip channel",
    "premium",
    "subscription",
]


def normalize_text(text: str) -> str:
    """Normalize GOLD references to XAUUSD for consistent parsing."""
    if not text:
        return ""
    # Replace whole-word GOLD (case-insensitive) with XAUUSD, but avoid replacing inside URLs/words.
    text = re.sub(r"\bGOLD\b", "XAUUSD", text, flags=re.IGNORECASE)
    return text


def extract_author(text: str) -> str | None:
    """Extract author signature like '--Trade by Mark' or 'Trade by William'."""
    if not text:
        return None
    patterns = [
        r"--\s*Trade by\s+([A-Za-z]+)",
        r"Trade by\s+([A-Za-z]+)",
        r"by\s+([A-Za-z]+)\s*$",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip().title()
    return None


def _is_noise(text: str) -> str | None:
    lower = text.lower()
    for kw in NOISE_KEYWORDS:
        if kw in lower:
            return f"noise_keyword:{kw}"
    for kw in AD_KEYWORDS:
        if kw in lower:
            return f"ad_keyword:{kw}"
    return None


def _classify(signal: TradeSignal | None, text: str) -> str:
    if signal is None:
        return "unknown"

    st = signal.signal_type
    if st == SignalType.NEW:
        if signal.order_type in (OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT):
            return "new_trade_pending"
        return "new_trade_market"
    if st in (SignalType.CLOSE, SignalType.CLOSE_HALF, SignalType.CLOSE_PARTIAL):
        return "close"
    if st == SignalType.SL_TO_ENTRY:
        return "move_sl"
    if st == SignalType.TP_HIT:
        return "tp_hit"
    if st == SignalType.SL_HIT:
        return "sl_hit"
    if st == SignalType.RISK_HIT:
        return "risk_hit"
    if st == SignalType.CANCEL:
        return "cancel"
    if st == SignalType.RUNNING:
        return "running"
    if st == SignalType.IGNORE:
        return "noise"
    return "unknown"


def _quality_flags(signal: TradeSignal | None, text: str) -> list[str]:
    flags: list[str] = []
    if signal is None or signal.signal_type != SignalType.NEW:
        return flags
    if signal.sl is None:
        flags.append("missing_sl")
    if not signal.take_profits:
        flags.append("missing_tp")
    if signal.sl_float is not None and signal.entry_price is not None:
        if signal.direction.value == "BUY" and signal.sl_float >= signal.entry_price:
            flags.append("corrupted_sl")
        if signal.direction.value == "SELL" and signal.sl_float <= signal.entry_price:
            flags.append("corrupted_sl")
    for tp in signal.take_profits:
        if signal.entry_price is not None:
            if signal.direction.value == "BUY" and tp <= signal.entry_price:
                flags.append("corrupted_tp")
            if signal.direction.value == "SELL" and tp >= signal.entry_price:
                flags.append("corrupted_tp")
    if "GOLD" in text.upper():
        flags.append("gold_synonym")
    return flags


def classify_messages(messages: list[dict[str, Any]]) -> list[ClassifiedMessage]:
    """Classify a list of raw Telegram messages."""
    results: list[ClassifiedMessage] = []
    for msg in messages:
        text = str(msg.get("text") or "").strip()
        raw_text = text
        noise_reason = _is_noise(text)
        if noise_reason:
            results.append(
                ClassifiedMessage(
                    message_id=int(msg.get("message_id", 0)),
                    date=msg.get("date", ""),
                    text=raw_text,
                    reply_to_message_id=msg.get("reply_to_message_id"),
                    author=None,
                    category="noise",
                    signal=None,
                    noise_reason=noise_reason,
                    quality_flags=[],
                )
            )
            continue

        normalized = normalize_text(text)
        signal = parse_signal(
            raw_text=normalized,
            message_id=int(msg.get("message_id", 0)),
            chat_id=str(msg.get("chat_id", "")),
            reply_to_message_id=msg.get("reply_to_message_id"),
            timestamp_ms=_date_to_ms(msg.get("date")),
        )

        author = extract_author(text)
        category = "noise" if (signal and signal.signal_type == SignalType.IGNORE) else _classify(signal, text)
        quality_flags = _quality_flags(signal, text)

        results.append(
            ClassifiedMessage(
                message_id=int(msg.get("message_id", 0)),
                date=msg.get("date", ""),
                text=raw_text,
                reply_to_message_id=msg.get("reply_to_message_id"),
                author=author,
                category=category,
                signal=signal,
                noise_reason=None,
                quality_flags=quality_flags,
            )
        )
    return results


def _date_to_ms(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return None
