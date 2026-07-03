"""Signal data models for the SSFX trading system."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from .enums import (
    Direction,
    ExecutionStatus,
    OrderType,
    SignalStatus,
    SignalType,
)


class TradeSignal(BaseModel):
    """Parsed trading signal from a Telegram channel."""

    raw_text: str
    direction: Direction | None = None
    symbol: str | None = None
    signal_type: SignalType = SignalType.NEW
    order_type: OrderType | None = None
    entry_price: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    sl: float | str | None = None
    profit_pips: int | None = None
    close_percentage: float | None = None
    follow_up_action: str | None = None
    tp_hit_number: int | None = None
    parse_confidence: float = 0.0

    # Telegram metadata
    message_id: int | None = None
    chat_id: str | None = None
    reply_to_message_id: int | None = None
    timestamp_ms: int = Field(default_factory=lambda: int(datetime.now(UTC).timestamp() * 1000))

    # Execution tracking (emitter-side)
    status: SignalStatus = SignalStatus.PENDING
    order_id: int | None = None
    position_id: int | None = None
    executed_price: float | None = None
    error: str | None = None

    # Parser provenance
    parser_used: str = "regex"
    llm_reasoning: str | None = None

    # Signal experience scoring (living quality context)
    quality_score: float | None = None
    quality_factors: dict[str, Any] | None = None
    experience_action: str | None = None  # allow / reduce / block
    volume_multiplier: float = 1.0

    @property
    def has_entry(self) -> bool:
        return self.entry_price is not None

    @property
    def has_sl(self) -> bool:
        return self.sl is not None and self.sl not in ("PREMIUM", "BREAKEVEN", "BE")

    @property
    def sl_float(self) -> float | None:
        if isinstance(self.sl, (int, float)):
            return float(self.sl)
        return None

    @property
    def take_profits(self) -> list[float]:
        return [tp for tp in (self.tp1, self.tp2, self.tp3) if tp is not None]




class RawMessage(BaseModel):
    """Raw Telegram message stored for parser context."""

    chat_id: str
    message_id: int
    text: str
    reply_to_message_id: int | None = None
    sender_id: int | None = None
    timestamp_ms: int = Field(default_factory=lambda: int(datetime.now(UTC).timestamp() * 1000))

    @property
    def date_str(self) -> str:
        dt = datetime.fromtimestamp(self.timestamp_ms / 1000, tz=UTC)
        return dt.strftime("%Y-%m-%d")




class SlaveExecution(BaseModel):
    """Per-slave execution tracking for a signal."""

    slave_id: str
    signal_chat_id: str
    signal_message_id: int
    signal_type: str = "NEW"
    status: ExecutionStatus = ExecutionStatus.PENDING
    order_id: int | None = None
    position_id: int | None = None
    executed_price: float | None = None
    volume: float | None = None
    original_volume_lots: float | None = None
    error: str | None = None
    skip_reason: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def signal_key(self) -> str:
        return f"{self.signal_chat_id}:{self.signal_message_id}"


