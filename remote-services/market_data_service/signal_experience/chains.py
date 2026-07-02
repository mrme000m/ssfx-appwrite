"""Build lifecycle chains from reply_to_message_id links."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ssfx_parser import TradeSignal

from .models import ClassifiedMessage, LifecycleChain, Outcome


def _pattern_key(signal: TradeSignal | None) -> str:
    if signal is None:
        return "UNKNOWN_UNKNOWN_MARKET"
    symbol = signal.symbol or "UNKNOWN"
    direction = signal.direction.value if signal.direction else "UNKNOWN"
    order_type = signal.order_type.value if signal.order_type else "MARKET"
    return f"{symbol}_{direction}_{order_type}"


def _parse_date(date_str: str) -> datetime:
    return datetime.fromisoformat(date_str.replace("Z", "+00:00"))


def _minutes_between(a: str, b: str) -> float:
    return (_parse_date(b) - _parse_date(a)).total_seconds() / 60.0


def _extract_pips_from_text(text: str) -> float | None:
    """Try to extract a pip number from close/tp text like '56+ PIPS'."""
    patterns = [
        r"(\d+)\+?\s*PIPS?",
        r"(\d+)\+?\s*PIPS?\s*PROFIT",
        r"TP\s*HIT.*?([\d\.]+)\+?\s*PIPS?",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _outcome_from_chain(entry: ClassifiedMessage, updates: list[ClassifiedMessage]) -> tuple[str, float | None, str | None]:
    """Derive outcome, pips, and closed_at from chain updates."""
    best_outcome = Outcome.OPEN
    pips: float | None = None
    closed_at: str | None = None

    for u in updates:
        cat = u.category
        sig = u.signal

        if cat == "tp_hit":
            best_outcome = Outcome.TP
            pips = _extract_pips_from_text(u.text) or sig.profit_pips if sig else None
            closed_at = u.date
            break  # TP is terminal

        if cat in ("sl_hit", "risk_hit"):
            best_outcome = Outcome.SL
            # Estimate loss as negative SL distance if known
            if sig and entry.signal and entry.signal.sl_float is not None and entry.signal.entry_price is not None:
                pips = (entry.signal.entry_price - entry.signal.sl_float) * 100
            closed_at = u.date
            break  # SL is terminal

        if cat == "move_sl":
            if best_outcome == Outcome.OPEN:
                best_outcome = Outcome.BE
            closed_at = u.date

        if cat == "close":
            if sig and sig.profit_pips is not None:
                pips = float(sig.profit_pips)
                best_outcome = Outcome.CLOSE_PROFIT if pips > 0 else Outcome.CLOSE_LOSS
            elif sig and sig.close_percentage is not None:
                best_outcome = Outcome.CLOSE_UNKNOWN
            else:
                extracted = _extract_pips_from_text(u.text)
                if extracted is not None:
                    pips = extracted
                    best_outcome = Outcome.CLOSE_PROFIT if pips > 0 else Outcome.CLOSE_LOSS
                else:
                    best_outcome = Outcome.CLOSE_UNKNOWN
            closed_at = u.date

        if cat == "cancel":
            best_outcome = Outcome.DELETED_PENDING
            closed_at = u.date

    return best_outcome, pips, closed_at


def build_chains(classified: list[ClassifiedMessage]) -> list[LifecycleChain]:
    """Build lifecycle chains from classified messages."""
    by_id = {c.message_id: c for c in classified}
    children: dict[int, list[int]] = {}
    for c in classified:
        if c.reply_to_message_id:
            children.setdefault(c.reply_to_message_id, []).append(c.message_id)

    # Start chains at entries that are not replies to other trade messages
    roots = [
        c for c in classified
        if c.is_new_trade and (not c.reply_to_message_id or c.reply_to_message_id not in by_id)
    ]

    chains: list[LifecycleChain] = []
    for root in roots:
        chain_ids: list[int] = []
        queue = list(children.get(root.message_id, []))
        visited = {root.message_id}
        while queue:
            mid = queue.pop(0)
            if mid in visited:
                continue
            visited.add(mid)
            chain_ids.append(mid)
            queue.extend(children.get(mid, []))

        updates = [by_id[mid] for mid in chain_ids if mid in by_id]
        updates.sort(key=lambda c: c.date)

        if updates:
            first_update_min = _minutes_between(root.date, updates[0].date)
            last_update_min = _minutes_between(root.date, updates[-1].date)
        else:
            first_update_min = None
            last_update_min = None

        outcome, outcome_pips, closed_at = _outcome_from_chain(root, updates)

        chains.append(
            LifecycleChain(
                entry_message_id=root.message_id,
                author=root.author,
                symbol=root.signal.symbol if root.signal else None,
                direction=root.signal.direction.value if root.signal and root.signal.direction else None,
                order_type=root.signal.order_type.value if root.signal and root.signal.order_type else None,
                entry_date=root.date,
                messages=[root] + updates,
                outcome=outcome,
                outcome_pips=outcome_pips,
                first_update_min=first_update_min,
                last_update_min=last_update_min,
                closed_at=closed_at,
                quality_flags=root.quality_flags,
            )
        )

    return chains
