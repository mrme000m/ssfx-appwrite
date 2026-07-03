"""Round-trip tests for account configuration serialization."""
from __future__ import annotations

import pytest

from ssfx_parser import ExecutionMode, SlStrategy, TpStrategy, VolumeMode
from ssfx_trader.config import AccountConfig


@pytest.fixture
def raw_account_doc() -> dict:
    return {
        "_id": "test",
        "name": "test",
        "enabled": True,
        "owner_id": "u1",
        "ctrader": {
            "broker_url": "https://auth-ctrader.example.com",
            "grant_id": "grant",
            "client_id": "cid",
            "client_secret": "secret",
            "account_id": 123,
            "host_type": "demo",
        },
        "trading": {
            "execution_mode": "demo",
            "volume_mode": "fixed_lots",
            "order_handling": "follow_signal",
            "tp_strategy": "tp1_only",
            "sl_strategy": "follow_signal",
            "partial_close": {
                "on_tp1_pct": 40.0,
                "on_tp2_pct": 30.0,
                "on_tp3_pct": 100.0,
                "on_close_half_pct": 50.0,
                "on_second_update_pct": 100.0,
            },
            "update_actions": {
                "second_update_action": "full_close",
                "entry_update_action": "ignore",
            },
            "symbol_overrides": [
                {
                    "symbol": "XAUUSD",
                    "enabled": True,
                    "volume_mode": "percent_risk",
                    "volume_value": 1.0,
                    "tp_strategy": "tp1_only",
                    "sl_strategy": "follow_signal",
                    "partial_close": {
                        "on_tp1_pct": 25.0,
                        "on_tp2_pct": 25.0,
                        "on_tp3_pct": 100.0,
                    },
                }
            ],
        },
        "symbols_filter": ["XAUUSD"],
    }


def test_from_mongo_coerces_string_enums(raw_account_doc: dict) -> None:
    cfg = AccountConfig.from_mongo(raw_account_doc)
    assert cfg.trading.execution_mode == ExecutionMode.DEMO
    assert cfg.trading.volume_mode == VolumeMode.FIXED_LOTS
    assert cfg.trading.tp_strategy == TpStrategy.TP1_ONLY
    assert cfg.trading.sl_strategy == SlStrategy.FOLLOW_SIGNAL

    override = cfg.trading.symbol_overrides[0]
    assert override.volume_mode == VolumeMode.PERCENT_RISK
    assert override.tp_strategy == TpStrategy.TP1_ONLY
    assert override.sl_strategy == SlStrategy.FOLLOW_SIGNAL
    assert override.partial_close is not None
    assert override.partial_close.on_tp1_pct == 25.0


def test_to_mongo_round_trip(raw_account_doc: dict) -> None:
    cfg = AccountConfig.from_mongo(raw_account_doc)
    serialized = cfg.to_mongo()

    assert serialized["_id"] == "test"
    assert serialized["trading"]["execution_mode"] == "demo"
    assert serialized["trading"]["volume_mode"] == "fixed_lots"
    assert serialized["trading"]["partial_close"]["on_tp1_pct"] == 40.0
    assert serialized["trading"]["update_actions"]["second_update_action"] == "full_close"

    override = serialized["trading"]["symbol_overrides"][0]
    assert override["volume_mode"] == "percent_risk"
    assert override["tp_strategy"] == "tp1_only"
    assert override["sl_strategy"] == "follow_signal"
    assert override["partial_close"]["on_tp1_pct"] == 25.0


def test_from_mongo_to_mongo_idempotency(raw_account_doc: dict) -> None:
    first = AccountConfig.from_mongo(raw_account_doc).to_mongo()
    second = AccountConfig.from_mongo(first).to_mongo()
    assert first == second
