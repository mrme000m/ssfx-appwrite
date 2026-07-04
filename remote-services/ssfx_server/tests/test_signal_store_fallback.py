"""Tests for ssfx-server signal-store backend selection and fallback."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from ssfx_server.config_loader import ServerConfig
from ssfx_server.web_app import _create_signal_store
from ssfx_trader.stores.noop_store import NoOpSignalStore
from ssfx_trader.stores.sqlite_signal_store import SQLiteSignalStore


def _minimal_config(**overrides: Any) -> ServerConfig:
    defaults: dict[str, Any] = {
        "telegram_bot_token": "t",
        "telegram_webhook_secret_token": "s",
        "source_chat_id": "c",
        "webhook_host": "https://example.com",
        "webhook_port": 8000,
        "webhook_path": "/webhook",
        "llm_api_key": "k",
        "llm_model": "m",
        "llm_base_url": "https://example.com/v1",
        "log_level": "INFO",
        "appwrite_endpoint": "https://example.com/v1",
        "appwrite_project_id": "p",
        "appwrite_api_key": "k",
        "appwrite_database_id": "d",
        "appwrite_accounts_table": "a",
        "appwrite_presets_table": "p",
        "appwrite_executions_table": "e",
        "appwrite_risk_state_table": "r",
        "dataservice_base_url": "http://ds",
        "dataservice_api_key": "k",
        "agent_harness_base_url": "http://ah",
        "agent_intent_enabled": False,
        "ctrader_broker_url": "",
        "admin_site_origin": "https://app.example.com",
        "admin_api_key": "k",
        "signal_experience_enabled": False,
        "signal_experience_database_id": "market_data",
        "signal_experience_block_threshold": 0.5,
        "signal_experience_reduce_threshold": 0.75,
        "agent_autonomy_enabled": False,
        "gold_quant_signal_enabled": False,
        "gold_quant_signal_interval_sec": 60.0,
        "gold_quant_min_confidence": 0.75,
        "gold_quant_agent_min_confidence": 0.65,
        "signal_store_backend": "appwrite",
        "signal_store_appwrite_database_id": "market_data",
        "appwrite_raw_messages_table": "raw_messages",
        "appwrite_parsed_signals_table": "parsed_signals",
        "appwrite_signal_trades_table": "signal_trades",
        "signal_store_sqlite_path": "/tmp/signals.db",
        "signal_store_fallback_sqlite": True,
        "signal_webhook_secret": "",
    }
    defaults.update(overrides)
    return ServerConfig(**defaults)


class TestCreateSignalStore:
    def test_appwrite_backend_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MagicMock()
        monkeypatch.setattr("ssfx_trader.stores.appwrite_signal_store.AppwriteSignalStore", mock)
        config = _minimal_config()
        store = _create_signal_store(config)
        assert store is mock.return_value
        mock.assert_called_once_with(database_id="market_data")

    def test_appwrite_backend_falls_back_to_sqlite(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        def _raise(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("appwrite down")
        monkeypatch.setattr("ssfx_trader.stores.appwrite_signal_store.AppwriteSignalStore", _raise)
        db_path = str(tmp_path / "fallback.db")
        config = _minimal_config(signal_store_sqlite_path=db_path)
        store = _create_signal_store(config)
        assert isinstance(store, SQLiteSignalStore)
        assert store.db_path == db_path

    def test_appwrite_backend_no_fallback_uses_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("appwrite down")
        monkeypatch.setattr("ssfx_trader.stores.appwrite_signal_store.AppwriteSignalStore", _raise)
        config = _minimal_config(signal_store_fallback_sqlite=False)
        store = _create_signal_store(config)
        assert isinstance(store, NoOpSignalStore)

    def test_sqlite_backend(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Ensure no Appwrite lookup is attempted.
        mock = MagicMock(side_effect=Exception("should not be called"))
        monkeypatch.setattr("ssfx_trader.stores.appwrite_signal_store.AppwriteSignalStore", mock)
        db_path = str(tmp_path / "sqlite.db")
        config = _minimal_config(signal_store_backend="sqlite", signal_store_sqlite_path=db_path)
        store = _create_signal_store(config)
        assert isinstance(store, SQLiteSignalStore)
        assert store.db_path == db_path
        mock.assert_not_called()

    def test_noop_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MagicMock(side_effect=Exception("should not be called"))
        monkeypatch.setattr("ssfx_trader.stores.appwrite_signal_store.AppwriteSignalStore", mock)
        config = _minimal_config(signal_store_backend="noop")
        store = _create_signal_store(config)
        assert isinstance(store, NoOpSignalStore)
        mock.assert_not_called()

    def test_unknown_backend_defaults_to_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MagicMock(side_effect=Exception("should not be called"))
        monkeypatch.setattr("ssfx_trader.stores.appwrite_signal_store.AppwriteSignalStore", mock)
        config = _minimal_config(signal_store_backend="magic")
        store = _create_signal_store(config)
        assert isinstance(store, NoOpSignalStore)
        mock.assert_not_called()
