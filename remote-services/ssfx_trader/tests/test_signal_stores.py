"""Tests for SQLite and Appwrite signal stores."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from ssfx_parser import Direction, RawMessage, SignalStatus, SignalType, TradeSignal
from ssfx_trader.stores.appwrite_signal_store import AppwriteSignalStore
from ssfx_trader.stores.sqlite_signal_store import SQLiteSignalStore


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _make_signal(**overrides: Any) -> TradeSignal:
    defaults = {
        "chat_id": "-1001661400724",
        "message_id": 42,
        "raw_text": "BUY XAUUSD @ 2000",
        "status": SignalStatus.PENDING,
        "direction": Direction.BUY,
        "symbol": "XAUUSD",
        "signal_type": SignalType.NEW,
        "entry_price": 2000.0,
        "parse_confidence": 0.9,
        "timestamp_ms": 1700000000000,
    }
    defaults.update(overrides)
    return TradeSignal(**defaults)


class TestSQLiteSignalStore:
    def test_save_and_get_signal(self, tmp_path: Path) -> None:
        db = str(tmp_path / "signals.db")
        store = SQLiteSignalStore(db)
        signal = _make_signal()
        assert store.save_signal(signal) is True
        fetched = store.get_signal(signal.chat_id, signal.message_id)
        assert fetched is not None
        assert fetched.direction == Direction.BUY
        assert fetched.symbol == "XAUUSD"
        assert fetched.entry_price == 2000.0

    def test_save_and_get_raw_message(self, tmp_path: Path) -> None:
        db = str(tmp_path / "signals.db")
        store = SQLiteSignalStore(db)
        ts = _now_ms()
        msg = RawMessage(
            chat_id="-1001661400724",
            message_id=1,
            text="hello",
            timestamp_ms=ts,
        )
        assert store.save_raw_message(msg) is True
        messages = store.get_today_messages()
        assert len(messages) == 1
        assert messages[0].text == "hello"

    def test_get_today_messages_filters_chat(self, tmp_path: Path) -> None:
        db = str(tmp_path / "signals.db")
        store = SQLiteSignalStore(db)
        ts = _now_ms()
        store.save_raw_message(RawMessage(chat_id="a", message_id=1, text="x", timestamp_ms=ts))
        store.save_raw_message(RawMessage(chat_id="b", message_id=2, text="y", timestamp_ms=ts))
        assert len(store.get_today_messages("a")) == 1
        assert store.get_today_messages("a")[0].chat_id == "a"

    def test_active_signals(self, tmp_path: Path) -> None:
        db = str(tmp_path / "signals.db")
        store = SQLiteSignalStore(db)
        store.save_signal(_make_signal(status=SignalStatus.PENDING))
        store.save_signal(_make_signal(message_id=2, status=SignalStatus.CLOSED))
        active = store.get_active_signals()
        assert len(active) == 1
        assert active[0].message_id == 42

    def test_update_signal_status(self, tmp_path: Path) -> None:
        db = str(tmp_path / "signals.db")
        store = SQLiteSignalStore(db)
        store.save_signal(_make_signal())
        store.update_signal_status(
            "-1001661400724", 42, SignalStatus.EXECUTED.value,
            order_id=7, position_id=77, executed_price=2001.5,
        )
        fetched = store.get_signal("-1001661400724", 42)
        assert fetched is not None
        assert fetched.status == SignalStatus.EXECUTED.value
        assert fetched.order_id == 7
        assert fetched.position_id == 77
        assert fetched.executed_price == 2001.5

    def test_record_trade(self, tmp_path: Path) -> None:
        db = str(tmp_path / "signals.db")
        store = SQLiteSignalStore(db)
        signal = _make_signal()
        store.record_trade(signal, {"pnl": 10.0})
        trades = store.list_trades()
        assert len(trades) == 1
        assert trades[0]["result"]["pnl"] == 10.0

    def test_get_pending_entry_signals(self, tmp_path: Path) -> None:
        db = str(tmp_path / "signals.db")
        store = SQLiteSignalStore(db)
        store.save_signal(_make_signal(signal_type=SignalType.NEW, status=SignalStatus.PENDING))
        store.save_signal(_make_signal(message_id=2, signal_type=SignalType.CLOSE, status=SignalStatus.PENDING))
        pending = store.get_pending_entry_signals()
        assert len(pending) == 1
        assert pending[0].message_id == 42


class TestAppwriteSignalStore:
    def _fake_tables(self) -> MagicMock:
        tables = MagicMock()
        tables.list_rows.return_value = MagicMock(rows=[])
        return tables

    def test_save_raw_message(self) -> None:
        tables = self._fake_tables()
        store = AppwriteSignalStore(tables_db=tables, verify_connection=False)
        msg = RawMessage(
            chat_id="-1001661400724",
            message_id=1,
            text="hello",
            timestamp_ms=1700000000000,
        )
        assert store.save_raw_message(msg) is True
        tables.upsert_row.assert_called_once()
        call = tables.upsert_row.call_args
        assert call.kwargs["table_id"] == "raw_messages"
        data = call.kwargs["data"]
        assert data["chat_id"] == msg.chat_id
        assert data["message_id"] == msg.message_id
        assert data["text"] == msg.text

    def test_save_signal(self) -> None:
        tables = self._fake_tables()
        store = AppwriteSignalStore(tables_db=tables, verify_connection=False)
        signal = _make_signal(quality_factors={"foo": "bar"})
        assert store.save_signal(signal) is True
        tables.upsert_row.assert_called_once()
        call = tables.upsert_row.call_args
        assert call.kwargs["table_id"] == "parsed_signals"
        assert call.kwargs["row_id"] == f"{signal.chat_id}_{signal.message_id}"[:36]
        data = call.kwargs["data"]
        assert data["direction"] == "BUY"
        assert data["quality_factors"] == '{"foo": "bar"}'

    def test_get_signal(self) -> None:
        tables = self._fake_tables()
        tables.get_row.return_value = MagicMock(
            data={
                "chat_id": "c",
                "message_id": 1,
                "raw_text": "t",
                "status": "pending",
                "direction": "BUY",
                "symbol": "XAUUSD",
                "signal_type": "NEW",
                "parser_used": "regex",
                "parse_confidence": 0.9,
                "volume_multiplier": 1.0,
                "timestamp_ms": 1700000000000,
            },
        )
        store = AppwriteSignalStore(tables_db=tables, verify_connection=False)
        signal = store.get_signal("c", 1)
        assert signal is not None
        assert signal.symbol == "XAUUSD"
        assert signal.parser_used == "regex"

    def test_get_signal_not_found(self) -> None:
        tables = self._fake_tables()
        exc = Exception()
        exc.code = 404
        tables.get_row.side_effect = exc
        store = AppwriteSignalStore(tables_db=tables, verify_connection=False)
        assert store.get_signal("c", 1) is None

    def test_get_active_signals_queries(self) -> None:
        tables = self._fake_tables()
        tables.list_rows.return_value = MagicMock(rows=[])
        store = AppwriteSignalStore(tables_db=tables, verify_connection=False)
        store.get_active_signals()
        queries = tables.list_rows.call_args.kwargs["queries"]
        assert any("or" in q for q in queries)

    def test_get_today_messages_filters_chat(self) -> None:
        tables = self._fake_tables()
        tables.list_rows.return_value = MagicMock(rows=[])
        store = AppwriteSignalStore(tables_db=tables, verify_connection=False)
        store.get_today_messages("-1001661400724")
        queries = tables.list_rows.call_args.kwargs["queries"]
        assert any('"equal","attribute":"chat_id"' in q for q in queries)
        assert any('"equal","attribute":"date"' in q for q in queries)

    def test_list_signals_orders_and_limits(self) -> None:
        tables = self._fake_tables()
        tables.list_rows.return_value = MagicMock(rows=[])
        store = AppwriteSignalStore(tables_db=tables, verify_connection=False)
        store.list_signals(25)
        queries = tables.list_rows.call_args.kwargs["queries"]
        assert any('"orderDesc","attribute":"timestamp_ms"' in q for q in queries)
        assert any('"limit"' in q and '"values":[25]' in q for q in queries)

    def test_record_trade(self) -> None:
        tables = self._fake_tables()
        store = AppwriteSignalStore(tables_db=tables, verify_connection=False)
        signal = _make_signal()
        store.record_trade(signal, {"foo": 1})
        tables.create_row.assert_called_once()
        call = tables.create_row.call_args
        assert call.kwargs["table_id"] == "signal_trades"
        data = call.kwargs["data"]
        assert data["result_json"] == '{"foo": 1}'

    def test_uses_custom_table_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APPWRITE_PARSED_SIGNALS_TABLE", "custom_signals")
        tables = self._fake_tables()
        store = AppwriteSignalStore(tables_db=tables, verify_connection=False)
        signal = _make_signal()
        store.save_signal(signal)
        assert tables.upsert_row.call_args.kwargs["table_id"] == "custom_signals"

    def test_strip_row_with_data_object(self) -> None:
        tables = self._fake_tables()
        class FakeRow:
            data = {"chat_id": "c"}
        from ssfx_trader.stores.appwrite_signal_store import _strip_row
        assert _strip_row(FakeRow()).get("chat_id") == "c"

    def test_strip_row_plain_dict(self) -> None:
        from ssfx_trader.stores.appwrite_signal_store import _strip_row
        assert _strip_row({"chat_id": "c"}).get("chat_id") == "c"
