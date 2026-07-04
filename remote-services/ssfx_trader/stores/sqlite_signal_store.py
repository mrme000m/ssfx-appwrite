"""SQLite-backed signal store for local development and small deployments.

This is the persistent replacement for NoOpSignalStore. It keeps raw messages,
parsed signals, and trade results in a local SQLite file so that the LLM agents
and operators have signal history, replay context, and duplicate-after-restart
protection even when an Appwrite-native signal store is not configured.

For production at scale, switch to the Appwrite-backed signal store once it
exists.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from ssfx_parser import Direction, OrderType, RawMessage, SignalStatus, SignalType, TradeSignal

logger = logging.getLogger(__name__)

_RAW_MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS raw_messages (
    chat_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    reply_to_message_id INTEGER,
    sender_id INTEGER,
    timestamp_ms INTEGER NOT NULL,
    date TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id)
)
"""

_PARSED_SIGNALS_DDL = """
CREATE TABLE IF NOT EXISTS parsed_signals (
    chat_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    direction TEXT,
    symbol TEXT,
    signal_type TEXT,
    order_type TEXT,
    entry_price REAL,
    sl TEXT,
    tp1 REAL,
    tp2 REAL,
    tp3 REAL,
    profit_pips INTEGER,
    close_percentage REAL,
    follow_up_action TEXT,
    tp_hit_number INTEGER,
    parse_confidence REAL NOT NULL DEFAULT 0.0,
    reply_to_message_id INTEGER,
    timestamp_ms INTEGER NOT NULL,
    parser_used TEXT NOT NULL DEFAULT 'regex',
    llm_reasoning TEXT,
    quality_score REAL,
    quality_factors TEXT,
    experience_action TEXT,
    volume_multiplier REAL NOT NULL DEFAULT 1.0,
    order_id INTEGER,
    position_id INTEGER,
    executed_price REAL,
    error TEXT,
    PRIMARY KEY (chat_id, message_id)
)
"""

_SIGNAL_TRADES_DDL = """
CREATE TABLE IF NOT EXISTS signal_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
)
"""

_INDICES_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_raw_messages_date ON raw_messages(date)",
    "CREATE INDEX IF NOT EXISTS idx_parsed_signals_status ON parsed_signals(status)",
    "CREATE INDEX IF NOT EXISTS idx_parsed_signals_timestamp ON parsed_signals(timestamp_ms DESC)",
    "CREATE INDEX IF NOT EXISTS idx_parsed_signals_symbol ON parsed_signals(symbol)",
]

_ACTIVE_STATUSES = {"pending", "emitted", "executed"}


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _date_str(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        timestamp_ms = int(datetime.now(UTC).timestamp() * 1000)
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _to_str(value: Any) -> str | None:
    """Return the string value of an enum or plain string."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    return str(value)


def _serialize_optional_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=str)


class SQLiteSignalStore:
    """Persistent signal store backed by a local SQLite database."""

    def __init__(self, db_path: str = "/app/data/signals.db") -> None:
        self.db_path = db_path
        self._ensure_schema()
        logger.info("SQLiteSignalStore initialized at %s", db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        import os

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._connect() as conn:
            conn.execute(_RAW_MESSAGES_DDL)
            conn.execute(_PARSED_SIGNALS_DDL)
            conn.execute(_SIGNAL_TRADES_DDL)
            for idx in _INDICES_DDL:
                conn.execute(idx)
            conn.commit()

    # ── Raw messages ──────────────────────────────────────────────────────────

    def save_raw_message(self, msg: RawMessage) -> bool:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO raw_messages
                (chat_id, message_id, text, reply_to_message_id, sender_id, timestamp_ms, date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.chat_id,
                    msg.message_id,
                    msg.text,
                    msg.reply_to_message_id,
                    msg.sender_id,
                    msg.timestamp_ms,
                    _date_str(msg.timestamp_ms),
                ),
            )
            conn.commit()
        return True

    def get_today_messages(self, chat_id: str | None = None) -> list[RawMessage]:
        today = _date_str(int(datetime.now(UTC).timestamp() * 1000))
        with self._connect() as conn:
            if chat_id:
                rows = conn.execute(
                    """
                    SELECT * FROM raw_messages
                    WHERE date = ? AND chat_id = ?
                    ORDER BY timestamp_ms
                    """,
                    (today, chat_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM raw_messages
                    WHERE date = ?
                    ORDER BY timestamp_ms
                    """,
                    (today,),
                ).fetchall()
        return [self._row_to_raw_message(row) for row in rows]

    # ── Parsed signals ────────────────────────────────────────────────────────

    def save_signal(self, signal: TradeSignal) -> bool:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO parsed_signals
                (chat_id, message_id, status, raw_text, direction, symbol, signal_type,
                 order_type, entry_price, sl, tp1, tp2, tp3, profit_pips, close_percentage,
                 follow_up_action, tp_hit_number, parse_confidence, reply_to_message_id,
                 timestamp_ms, parser_used, llm_reasoning, quality_score, quality_factors,
                 experience_action, volume_multiplier, order_id, position_id, executed_price, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._signal_to_row(signal),
            )
            conn.commit()
        return True

    def get_signal(self, chat_id: str, message_id: int) -> TradeSignal | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM parsed_signals WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            ).fetchone()
        return self._row_to_signal(row) if row else None

    def get_active_signals(self, chat_id: str | None = None) -> list[TradeSignal]:
        statuses = tuple(_ACTIVE_STATUSES)
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as conn:
            if chat_id:
                rows = conn.execute(
                    f"""
                    SELECT * FROM parsed_signals
                    WHERE status IN ({placeholders}) AND chat_id = ?
                    ORDER BY timestamp_ms
                    """,
                    statuses + (chat_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT * FROM parsed_signals
                    WHERE status IN ({placeholders})
                    ORDER BY timestamp_ms
                    """,
                    statuses,
                ).fetchall()
        return [self._row_to_signal(row) for row in rows]

    def get_pending_entry_signals(self) -> list[TradeSignal]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM parsed_signals
                WHERE status = ? AND signal_type = ?
                ORDER BY timestamp_ms
                """,
                (SignalStatus.PENDING.value, SignalType.NEW.value),
            ).fetchall()
        return [self._row_to_signal(row) for row in rows]

    def update_signal_status(
        self,
        chat_id: str,
        message_id: int,
        status: str,
        order_id: int | None = None,
        position_id: int | None = None,
        executed_price: float | None = None,
        error: str | None = None,
        volume: float | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE parsed_signals
                SET status = ?, order_id = ?, position_id = ?, executed_price = ?, error = ?
                WHERE chat_id = ? AND message_id = ?
                """,
                (status, order_id, position_id, executed_price, error, chat_id, message_id),
            )
            conn.commit()

    def update_signal_entry(self, chat_id: str, message_id: int, entry_price: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE parsed_signals SET entry_price = ? WHERE chat_id = ? AND message_id = ?",
                (entry_price, chat_id, message_id),
            )
            conn.commit()

    # ── Trade records and listing ─────────────────────────────────────────────

    def record_trade(self, signal: TradeSignal, result: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO signal_trades (chat_id, message_id, result_json, recorded_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    signal.chat_id,
                    signal.message_id,
                    json.dumps(result, default=str),
                    _iso_now(),
                ),
            )
            conn.commit()

    def list_signals(self, limit: int = 50) -> list[TradeSignal]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM parsed_signals
                ORDER BY timestamp_ms DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_signal(row) for row in rows]

    def list_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM signal_trades
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "chat_id": row["chat_id"],
                "message_id": row["message_id"],
                "result": json.loads(row["result_json"]),
                "recorded_at": row["recorded_at"],
            }
            for row in rows
        ]

    async def watch_signals(self) -> AsyncIterator[TradeSignal]:
        """Async stream placeholder — SQLite does not have a live pub/sub channel."""
        logger.debug("SQLiteSignalStore.watch_signals is a no-op stream")
        if False:
            yield  # type: ignore[unreachable]

    # ── Serialization helpers ─────────────────────────────────────────────────

    @staticmethod
    def _signal_to_row(signal: TradeSignal) -> tuple:
        return (
            signal.chat_id,
            signal.message_id,
            _to_str(signal.status) or SignalStatus.PENDING.value,
            signal.raw_text,
            _to_str(signal.direction),
            signal.symbol,
            _to_str(signal.signal_type),
            _to_str(signal.order_type),
            signal.entry_price,
            None if signal.sl is None else str(signal.sl),
            signal.tp1,
            signal.tp2,
            signal.tp3,
            signal.profit_pips,
            signal.close_percentage,
            signal.follow_up_action,
            signal.tp_hit_number,
            signal.parse_confidence,
            signal.reply_to_message_id,
            signal.timestamp_ms,
            signal.parser_used,
            signal.llm_reasoning,
            signal.quality_score,
            _serialize_optional_json(signal.quality_factors),
            signal.experience_action,
            signal.volume_multiplier,
            signal.order_id,
            signal.position_id,
            signal.executed_price,
            signal.error,
        )

    @staticmethod
    def _row_to_signal(row: sqlite3.Row) -> TradeSignal:
        kwargs: dict[str, Any] = {
            "chat_id": row["chat_id"],
            "message_id": row["message_id"],
            "raw_text": row["raw_text"],
            "status": row["status"],
            "direction": row["direction"],
            "symbol": row["symbol"],
            "signal_type": row["signal_type"],
            "order_type": row["order_type"],
            "entry_price": row["entry_price"],
            "sl": row["sl"],
            "tp1": row["tp1"],
            "tp2": row["tp2"],
            "tp3": row["tp3"],
            "profit_pips": row["profit_pips"],
            "close_percentage": row["close_percentage"],
            "follow_up_action": row["follow_up_action"],
            "tp_hit_number": row["tp_hit_number"],
            "parse_confidence": row["parse_confidence"],
            "reply_to_message_id": row["reply_to_message_id"],
            "timestamp_ms": row["timestamp_ms"],
            "parser_used": row["parser_used"],
            "llm_reasoning": row["llm_reasoning"],
            "quality_score": row["quality_score"],
            "experience_action": row["experience_action"],
            "volume_multiplier": row["volume_multiplier"],
            "order_id": row["order_id"],
            "position_id": row["position_id"],
            "executed_price": row["executed_price"],
            "error": row["error"],
        }
        if row["quality_factors"]:
            kwargs["quality_factors"] = json.loads(row["quality_factors"])
        return TradeSignal(**kwargs)

    @staticmethod
    def _row_to_raw_message(row: sqlite3.Row) -> RawMessage:
        return RawMessage(
            chat_id=row["chat_id"],
            message_id=row["message_id"],
            text=row["text"],
            reply_to_message_id=row["reply_to_message_id"],
            sender_id=row["sender_id"],
            timestamp_ms=row["timestamp_ms"],
        )
