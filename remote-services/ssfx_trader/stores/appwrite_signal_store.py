"""Appwrite TablesDB-backed signal store.

This is the cloud-native primary signal store. It stores raw messages, parsed
signals and trade records in Appwrite TablesDB so the pipeline has a shared
source of truth across restarts and replicas.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from appwrite.exception import AppwriteException
from appwrite.id import ID
from appwrite.services.tables_db import TablesDB

from shared.appwrite_client import create_appwrite_client
from ssfx_parser import Direction, OrderType, RawMessage, SignalStatus, SignalType, TradeSignal

logger = logging.getLogger(__name__)

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
    if isinstance(value, Direction | OrderType | SignalStatus | SignalType):
        return value.value
    return str(value)


def _serialize_optional_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=str)


def _strip_row(row: Any) -> dict[str, Any]:
    """Return user-defined data fields from an Appwrite row."""
    if hasattr(row, "data"):
        data = row.data
        return dict(data) if data is not None else {}
    if hasattr(row, "to_dict"):
        d = row.to_dict()
    elif isinstance(row, dict):
        d = row
    else:
        d = dict(row)
    if isinstance(d.get("data"), dict):
        return dict(d["data"])
    return {k: v for k, v in d.items() if not k.startswith("$")}


def _row_id_for(chat_id: str, message_id: int) -> str:
    # Appwrite row IDs must be <=36 chars and contain only a-zA-Z0-9_.
    return f"{chat_id}_{message_id}"[:36]


class AppwriteSignalStore:
    """Signal store backed by Appwrite TablesDB."""

    def __init__(
        self,
        database_id: str | None = None,
        raw_messages_table: str | None = None,
        parsed_signals_table: str | None = None,
        signal_trades_table: str | None = None,
        tables_db: TablesDB | None = None,
        verify_connection: bool = True,
    ) -> None:
        self.database_id = database_id or os.getenv("APPWRITE_SIGNAL_DATABASE_ID", "market_data")
        self.raw_messages_table = raw_messages_table or os.getenv("APPWRITE_RAW_MESSAGES_TABLE", "raw_messages")
        self.parsed_signals_table = parsed_signals_table or os.getenv("APPWRITE_PARSED_SIGNALS_TABLE", "parsed_signals")
        self.signal_trades_table = signal_trades_table or os.getenv("APPWRITE_SIGNAL_TRADES_TABLE", "signal_trades")

        if tables_db is None:
            _, tables_db = create_appwrite_client()
        self._tables = tables_db

        if verify_connection:
            # Validate credentials/network with a lightweight operation.
            try:
                self._tables.list_tables(database_id=self.database_id)
            except Exception as exc:
                logger.error("AppwriteSignalStore failed health check: %s", exc)
                _, tb = sys.exc_info()[2]
                if tb is not None:
                    traceback.print_exc()
                raise RuntimeError(f"Unable to connect to Appwrite TablesDB: {exc}") from exc

        logger.info(
            "AppwriteSignalStore initialized for database=%s tables=%s,%s,%s",
            self.database_id,
            self.raw_messages_table,
            self.parsed_signals_table,
            self.signal_trades_table,
        )

    # ── Raw messages ──────────────────────────────────────────────────────────

    def save_raw_message(self, msg: RawMessage) -> bool:
        row_id = _row_id_for(msg.chat_id, msg.message_id)
        body: dict[str, Any] = {
            "chat_id": msg.chat_id,
            "message_id": msg.message_id,
            "text": msg.text,
            "reply_to_message_id": msg.reply_to_message_id,
            "sender_id": msg.sender_id,
            "timestamp_ms": msg.timestamp_ms,
            "date": _date_str(msg.timestamp_ms),
        }
        try:
            self._tables.upsert_row(
                database_id=self.database_id,
                table_id=self.raw_messages_table,
                row_id=row_id,
                data=body,
            )
            return True
        except Exception as exc:
            logger.error("Failed to save raw message %s:%s: %s", msg.chat_id, msg.message_id, exc)
            return False

    def get_today_messages(self, chat_id: str | None = None) -> list[RawMessage]:
        from appwrite.query import Query

        today = _date_str(int(datetime.now(UTC).timestamp() * 1000))
        queries: list[str] = [
            Query.equal("date", today),
            Query.order_asc("timestamp_ms"),
            Query.limit(1000),
        ]
        if chat_id:
            queries.insert(0, Query.equal("chat_id", chat_id))

        rows = self._list_rows(self.raw_messages_table, queries)
        return [self._row_to_raw_message(r) for r in rows]

    # ── Parsed signals ────────────────────────────────────────────────────────

    def save_signal(self, signal: TradeSignal) -> bool:
        row_id = _row_id_for(signal.chat_id, signal.message_id)
        body = self._signal_to_row(signal)
        try:
            self._tables.upsert_row(
                database_id=self.database_id,
                table_id=self.parsed_signals_table,
                row_id=row_id,
                data=body,
            )
            return True
        except Exception as exc:
            logger.error("Failed to save signal %s:%s: %s", signal.chat_id, signal.message_id, exc)
            return False

    def get_signal(self, chat_id: str, message_id: int) -> TradeSignal | None:
        try:
            row = self._tables.get_row(
                database_id=self.database_id,
                table_id=self.parsed_signals_table,
                row_id=_row_id_for(chat_id, message_id),
            )
            return self._row_to_signal(_strip_row(row))
        except AppwriteException as e:
            if e.code == 404:
                return None
            logger.error("Failed to get signal %s:%s: %s", chat_id, message_id, e)
            return None
        except Exception as exc:
            logger.error("Failed to get signal %s:%s: %s", chat_id, message_id, exc)
            return None

    def get_active_signals(self, chat_id: str | None = None) -> list[TradeSignal]:
        from appwrite.query import Query

        status_queries = [Query.equal("status", s) for s in _ACTIVE_STATUSES]
        queries: list[str] = [
            Query.or_queries(status_queries),
            Query.order_asc("timestamp_ms"),
            Query.limit(1000),
        ]
        if chat_id:
            queries.insert(0, Query.equal("chat_id", chat_id))

        rows = self._list_rows(self.parsed_signals_table, queries)
        return [self._row_to_signal(r) for r in rows]

    def get_pending_entry_signals(self) -> list[TradeSignal]:
        from appwrite.query import Query

        queries: list[str] = [
            Query.and_queries([
                Query.equal("status", SignalStatus.PENDING.value),
                Query.equal("signal_type", SignalType.NEW.value),
            ]),
            Query.order_asc("timestamp_ms"),
            Query.limit(1000),
        ]
        rows = self._list_rows(self.parsed_signals_table, queries)
        return [self._row_to_signal(r) for r in rows]

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
        row_id = _row_id_for(chat_id, message_id)
        body: dict[str, Any] = {
            "status": status,
            "order_id": order_id,
            "position_id": position_id,
            "executed_price": executed_price,
            "error": error,
        }
        # volume is accepted by the protocol but not part of the signal schema
        try:
            self._tables.update_row(
                database_id=self.database_id,
                table_id=self.parsed_signals_table,
                row_id=row_id,
                data=body,
            )
        except Exception as exc:
            logger.error("Failed to update signal status %s:%s: %s", chat_id, message_id, exc)

    def update_signal_entry(self, chat_id: str, message_id: int, entry_price: float) -> None:
        row_id = _row_id_for(chat_id, message_id)
        try:
            self._tables.update_row(
                database_id=self.database_id,
                table_id=self.parsed_signals_table,
                row_id=row_id,
                data={"entry_price": entry_price},
            )
        except Exception as exc:
            logger.error("Failed to update signal entry %s:%s: %s", chat_id, message_id, exc)

    # ── Trade records and listing ─────────────────────────────────────────────

    def record_trade(self, signal: TradeSignal, result: dict[str, Any]) -> None:
        body = {
            "chat_id": signal.chat_id,
            "message_id": signal.message_id,
            "result_json": json.dumps(result, default=str),
            "recorded_at": _iso_now(),
        }
        try:
            self._tables.create_row(
                database_id=self.database_id,
                table_id=self.signal_trades_table,
                row_id=ID.unique(),
                data=body,
            )
        except Exception as exc:
            logger.error("Failed to record trade %s:%s: %s", signal.chat_id, signal.message_id, exc)

    def list_signals(self, limit: int = 50) -> list[TradeSignal]:
        from appwrite.query import Query

        queries = [Query.order_desc("timestamp_ms"), Query.limit(limit)]
        rows = self._list_rows(self.parsed_signals_table, queries)
        return [self._row_to_signal(r) for r in rows]

    def list_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        from appwrite.query import Query

        queries = [Query.order_desc("$createdAt"), Query.limit(limit)]
        rows = self._list_rows(self.signal_trades_table, queries)
        return [
            {
                "chat_id": r.get("chat_id"),
                "message_id": r.get("message_id"),
                "result": json.loads(r.get("result_json", "{}")),
                "recorded_at": r.get("recorded_at"),
            }
            for r in rows
        ]

    async def watch_signals(self) -> AsyncIterator[TradeSignal]:
        """Async stream placeholder — Appwrite Realtime is not wired here."""
        logger.debug("AppwriteSignalStore.watch_signals is a no-op stream")
        if False:
            yield  # type: ignore[unreachable]

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _list_rows(self, table_id: str, queries: list[str]) -> list[dict[str, Any]]:
        try:
            result = self._tables.list_rows(
                database_id=self.database_id,
                table_id=table_id,
                queries=queries,
            )
            if hasattr(result, "rows"):
                return [_strip_row(r) for r in result.rows]
            d = result.to_dict() if hasattr(result, "to_dict") else dict(result)
            return [_strip_row(r) for r in d.get("rows", [])]
        except Exception as exc:
            logger.error("Failed to list rows from %s: %s", table_id, exc)
            return []

    @staticmethod
    def _signal_to_row(signal: TradeSignal) -> dict[str, Any]:
        return {
            "chat_id": signal.chat_id,
            "message_id": signal.message_id,
            "status": _to_str(signal.status) or SignalStatus.PENDING.value,
            "raw_text": signal.raw_text,
            "direction": _to_str(signal.direction),
            "symbol": signal.symbol,
            "signal_type": _to_str(signal.signal_type),
            "order_type": _to_str(signal.order_type),
            "entry_price": signal.entry_price,
            "sl": None if signal.sl is None else str(signal.sl),
            "tp1": signal.tp1,
            "tp2": signal.tp2,
            "tp3": signal.tp3,
            "profit_pips": signal.profit_pips,
            "close_percentage": signal.close_percentage,
            "follow_up_action": signal.follow_up_action,
            "tp_hit_number": signal.tp_hit_number,
            "parse_confidence": signal.parse_confidence,
            "reply_to_message_id": signal.reply_to_message_id,
            "timestamp_ms": signal.timestamp_ms,
            "parser_used": signal.parser_used,
            "llm_reasoning": signal.llm_reasoning,
            "quality_score": signal.quality_score,
            "quality_factors": _serialize_optional_json(signal.quality_factors),
            "experience_action": signal.experience_action,
            "volume_multiplier": signal.volume_multiplier,
            "order_id": signal.order_id,
            "position_id": signal.position_id,
            "executed_price": signal.executed_price,
            "error": signal.error,
        }

    @staticmethod
    def _row_to_signal(row: dict[str, Any]) -> TradeSignal:
        kwargs: dict[str, Any] = {
            "chat_id": row.get("chat_id"),
            "message_id": row.get("message_id"),
            "raw_text": row.get("raw_text") or "",
            "status": row.get("status"),
            "direction": row.get("direction"),
            "symbol": row.get("symbol"),
            "signal_type": row.get("signal_type"),
            "order_type": row.get("order_type"),
            "entry_price": row.get("entry_price"),
            "sl": row.get("sl"),
            "tp1": row.get("tp1"),
            "tp2": row.get("tp2"),
            "tp3": row.get("tp3"),
            "profit_pips": row.get("profit_pips"),
            "close_percentage": row.get("close_percentage"),
            "follow_up_action": row.get("follow_up_action"),
            "tp_hit_number": row.get("tp_hit_number"),
            "parse_confidence": row.get("parse_confidence"),
            "reply_to_message_id": row.get("reply_to_message_id"),
            "timestamp_ms": row.get("timestamp_ms"),
            "parser_used": row.get("parser_used") or "regex",
            "llm_reasoning": row.get("llm_reasoning"),
            "quality_score": row.get("quality_score"),
            "quality_factors": row.get("quality_factors"),
            "experience_action": row.get("experience_action"),
            "volume_multiplier": row.get("volume_multiplier"),
            "order_id": row.get("order_id"),
            "position_id": row.get("position_id"),
            "executed_price": row.get("executed_price"),
            "error": row.get("error"),
        }
        if kwargs.get("quality_factors") and isinstance(kwargs["quality_factors"], str):
            try:
                kwargs["quality_factors"] = json.loads(kwargs["quality_factors"])
            except Exception:
                kwargs["quality_factors"] = None
        return TradeSignal(**kwargs)

    @staticmethod
    def _row_to_raw_message(row: dict[str, Any]) -> RawMessage:
        return RawMessage(
            chat_id=row.get("chat_id") or "",
            message_id=row.get("message_id") or 0,
            text=row.get("text") or "",
            reply_to_message_id=row.get("reply_to_message_id"),
            sender_id=row.get("sender_id"),
            timestamp_ms=row.get("timestamp_ms") or 0,
        )
