"""Regex signal parser for SureShot GOLD (VIP) Telegram messages."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from .enums import Direction, OrderType, SignalType
from .models import TradeSignal


def _to_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        cleaned = str(value).rstrip(".")
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def _strip_emoji(text: str) -> str:
    return re.sub(
        r"[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F\u200D\u00A0\u2022\u2714\u2757\u26AA\u26AB\u2B06\u2B07]",
        " ",
        text,
    )


def _tokenize(text: str) -> list[str]:
    return [
        token
        for token in re.sub(r"[,;|@$#]", " ", str(text or "")).split()
        if token
    ]


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _parse_first_number(text: str) -> float | None:
    for token in _tokenize(text):
        value = _to_number(token)
        if value is not None:
            return value
    return None


def _parse_pips(text: str) -> int | None:
    match = re.search(r"([0-9]+)\+?\s*pips?", str(text or ""), re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _value_after_keyword(line: str, keyword: str) -> float | None:
    lower = line.lower()
    idx = lower.find(keyword)
    if idx == -1:
        return None
    after = re.sub(r"^\s*[:=@\-.]?\s*", "", line[idx + len(keyword):])
    return _parse_first_number(after)


def _extract_symbol(text: str) -> str | None:
    lower = text.lower()
    if any(a in lower for a in ("xauusd", "gold", "xau ")):
        return "XAUUSD"
    if any(a in lower for a in ("btcusd", "bitcoin", "btc ")):
        return "BTCUSD"
    if any(a in lower for a in ("ethusd", "ethereum", "eth ")):
        return "ETHUSD"
    for token in _tokenize(text):
        candidate = token.upper().replace("/", "")
        if re.fullmatch(r"[A-Z]{3,6}(?:USD|USDT|USDC|JPY|EUR|GBP|AUD|CAD|CHF|NZD)", candidate):
            return candidate
    return None


def _detect_direction(text: str) -> Direction | None:
    for token in _tokenize(text):
        tl = token.lower()
        if tl == "buy":
            return Direction.BUY
        if tl == "sell":
            return Direction.SELL
        if tl in ("close", "half", "partial", "full", "tp", "running", "cancel"):
            return None
    return None


def _extract_order_type(text: str, direction: Direction | None) -> OrderType | None:
    lower = text.lower()
    if "stop limit" in lower:
        return OrderType.STOP_LIMIT
    if direction:
        d = direction.value.lower()
        if re.search(rf"\b{d}\s+limit\b", lower):
            return OrderType.LIMIT
        if re.search(rf"\b{d}\s+stop\b", lower):
            return OrderType.STOP
    if "buy limit" in lower or "sell limit" in lower:
        return OrderType.LIMIT
    if "buy stop" in lower or "sell stop" in lower:
        return OrderType.STOP
    return None


def _extract_entry(text: str, direction: Direction | None) -> float | None:
    lines = _split_lines(text)

    for line in lines:
        lower = line.lower()
        if "entry" in lower and "take entry" not in lower:
            value = _value_after_keyword(line, "entry")
            if value is not None:
                return value

    for line in lines:
        lower = line.lower()
        if direction:
            d = direction.value.lower()
            if d in lower:
                idx = lower.find(d)
                tail = line[idx + len(d):]
                tail_clean = re.sub(r"^(limit|stop|now)\s*", "", tail.strip(), flags=re.IGNORECASE)
                value = _parse_first_number(tail_clean)
                if value is not None:
                    return value

    for line in lines:
        if "take entry" in line.lower():
            value = _value_after_keyword(line, "entry")
            if value is not None:
                return value

    return None


def _extract_stop_loss(text: str) -> float | str | None:
    lines = _split_lines(text)
    keywords = ["stop loss", "stoploss", "stop_loss", "sl", "stop"]

    for line in lines:
        lower = line.lower()
        keyword = next((kw for kw in keywords if kw in lower), None)
        if not keyword:
            continue

        idx = lower.find(keyword)
        after = re.sub(r"^[\s:.\-]*", "", line[idx + len(keyword):])
        lower_after = after.lower()

        if "breakeven" in lower_after or "break even" in lower_after:
            return "BREAKEVEN"
        if lower_after.startswith("be ") or lower_after.strip() == "be":
            return "BE"

        numeric = _parse_first_number(after)
        if numeric is not None:
            return numeric

    return None


def _extract_take_profit(text: str, which: int = 0) -> float | None:
    aliases = [f"tp{which}", f"tp {which}", f"take profit {which}"] if which > 0 else ["tp", "take profit"]
    if which == 0:
        aliases = ["tp", "take profit"]

    lines = _split_lines(text)

    for line in lines:
        lower = line.lower()
        for alias in aliases:
            if alias not in lower:
                continue
            if alias in ("tp", "take profit") and ("tp1" in lower or "tp2" in lower or "tp3" in lower):
                continue
            value = _value_after_keyword(line, alias)
            if value is not None:
                return value

    return None


def _extract_all_take_profits(text: str) -> tuple[float | None, float | None, float | None]:
    tp1 = _extract_take_profit(text, 1)
    tp2 = _extract_take_profit(text, 2)
    tp3 = _extract_take_profit(text, 3)
    if tp1 is None:
        tp1 = _extract_take_profit(text, 0)
    return tp1, tp2, tp3


def _extract_close_percentage(text: str) -> float | None:
    lower = text.lower()
    if any(p in lower for p in ("close half", "close 50%", "close up half", "taking 50%")):
        return 50.0
    if any(p in lower for p in ("close partial",)):
        match = re.search(r"close\s+partial\s+([0-9]{1,3}(?:\.[0-9]+)?)\s*%", lower)
        if match:
            value = _to_number(match.group(1))
            if value is not None and 0 < value <= 100:
                return float(value)
        return 50.0
    if "close all" in lower or "close full" in lower:
        return 100.0
    return None


def _detect_signal_type(
    text: str,
    has_entry: bool,
    has_direction: bool,
    has_symbol: bool = False,
) -> tuple[SignalType, int | None]:
    lower = text.lower()

    cancel_patterns = [
        "cancel signal", "cancel this signal", "cancel order",
        "delete pending", "delete order", "ignore previous",
        "ignore signal", "do not enter", "don't enter", "invalid signal",
    ]
    if any(p in lower for p in cancel_patterns):
        return SignalType.CANCEL, None

    match = re.search(r"tp\s*([123])\s*hit", lower)
    if match:
        return SignalType.TP_HIT, int(match.group(1))
    if "tp1 hit" in lower:
        return SignalType.TP_HIT, 1
    if "tp2 hit" in lower:
        return SignalType.TP_HIT, 2
    if "tp3 hit" in lower:
        return SignalType.TP_HIT, 3

    tp_full_patterns = [
        "full tp hit", "all tp hit", "all targets hit",
        "all done", "tp smashed", "tp reached",
        "full tp", "all target",
    ]
    for p in tp_full_patterns:
        if p in lower:
            return SignalType.TP_HIT, 3
    if "booked" in lower and "profit" in lower:
        return SignalType.TP_HIT, 3

    if has_symbol and any(p in lower for p in ("close half", "close 50%", "close up half", "taking 50%")):
        return SignalType.CLOSE_HALF, None

    if has_symbol and any(p in lower for p in ("close partial", "partial close", "taking partial")):
        return SignalType.CLOSE_PARTIAL, None

    if any(p in lower for p in ("hit sl", "sl hit", "stopped out", "stop loss hit", "hit stop")):
        return SignalType.SL_HIT, None

    if "hit our risk" in lower or "hit risk" in lower:
        return SignalType.RISK_HIT, None

    be_patterns = [
        "move sl to entry", "sl to entry", "sl to be",
        "breakeven", "break even", "be reached",
        "move stop to entry", "stop to be", "stop to entry",
    ]
    if any(p in lower for p in be_patterns):
        return SignalType.SL_TO_ENTRY, None

    if "close" in lower and "running" not in lower:
        if not any(p in lower for p in ("close half", "close partial", "close 50%", "close up half")):
            return SignalType.CLOSE, None

    if ("running" in lower or "floating" in lower) and "pips" in lower:
        return SignalType.RUNNING, None

    if "take entry" in lower:
        return SignalType.ENTRY_UPDATE, None

    if has_direction and (has_entry or "signal alert" in lower or "buy now" in lower or "sell now" in lower):
        return SignalType.NEW, None

    return SignalType.IGNORE, None


def parse_signal(
    raw_text: str,
    message_id: int | None = None,
    chat_id: str | None = None,
    reply_to_message_id: int | None = None,
    timestamp_ms: int | None = None,
) -> TradeSignal | None:
    """Parse a raw Telegram message into a TradeSignal."""
    text = str(raw_text or "").strip()
    if not text:
        return None

    clean = _strip_emoji(text)

    direction = _detect_direction(clean)
    entry_price = _extract_entry(clean, direction)
    tp1, tp2, tp3 = _extract_all_take_profits(clean)
    sl = _extract_stop_loss(clean)
    order_type = _extract_order_type(clean, direction)
    symbol = _extract_symbol(clean)
    profit_pips = _parse_pips(clean)
    close_percentage = _extract_close_percentage(clean)

    signal_type, tp_hit_number = _detect_signal_type(
        clean, entry_price is not None, direction is not None, symbol is not None
    )

    follow_up_action = None
    mixed_close_pct = None
    lower = clean.lower()

    if signal_type == SignalType.TP_HIT:
        if any(p in lower for p in ("close up half profit", "close half profit", "close up half")):
            mixed_close_pct = 50.0
            follow_up_action = "CLOSE_HALF"
        elif any(p in lower for p in ("all target hit", "all targets hit", "all target done")):
            mixed_close_pct = 100.0
            follow_up_action = "CLOSE"
        elif "close partial" in lower:
            mixed_close_pct = _extract_close_percentage(clean) or 50.0
            follow_up_action = "CLOSE_PARTIAL"

    if signal_type in (SignalType.CLOSE_HALF, SignalType.CLOSE_PARTIAL):
        if any(p in lower for p in ("move sl to entry", "sl to entry", "sl to be", "breakeven")):
            follow_up_action = "SL_TO_ENTRY"

    if mixed_close_pct is not None:
        close_percentage = mixed_close_pct

    confidence = 0.35
    if direction:
        confidence += 0.2
    if symbol:
        confidence += 0.15
    if entry_price is not None:
        confidence += 0.15
    if tp1 is not None or tp2 is not None or tp3 is not None:
        confidence += 0.1
    if sl is not None:
        confidence += 0.05
    if signal_type not in (SignalType.NEW, SignalType.IGNORE):
        confidence = max(confidence, 0.80)
    if signal_type == SignalType.IGNORE:
        confidence = min(confidence, 0.30)

    if signal_type == SignalType.IGNORE and confidence < 0.50:
        return None

    return TradeSignal(
        raw_text=text,
        direction=direction,
        symbol=symbol,
        signal_type=signal_type,
        order_type=order_type,
        entry_price=entry_price,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        sl=sl,
        profit_pips=profit_pips,
        close_percentage=close_percentage,
        follow_up_action=follow_up_action,
        tp_hit_number=tp_hit_number,
        parse_confidence=round(min(confidence, 0.99), 2),
        message_id=message_id,
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        timestamp_ms=timestamp_ms or int(datetime.now(UTC).timestamp() * 1000),
    )
