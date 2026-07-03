"""Unit tests for Telegram webhook and admin API authentication."""
from __future__ import annotations

import pytest
from fastapi import HTTPException, Request

from ssfx_server import admin_api
from ssfx_server.config_loader import ServerConfig


class _FakeState:
    def __init__(self, admin_api_key: str = ""):
        self.config = ServerConfig(
            telegram_bot_token="bot_token",
            telegram_webhook_secret_token="webhook_secret",
            mongo_uri="mongodb://localhost:27017",
            mongo_database="ssfx_v2",
            source_chat_id="-1001661400724",
            webhook_host="https://ssfx-api.mrme.tech",
            webhook_port=8000,
            webhook_path="/webhook",
            llm_api_key="",
            llm_model="",
            llm_base_url="",
            log_level="INFO",
            appwrite_endpoint="",
            appwrite_project_id="",
            appwrite_api_key="",
            appwrite_database_id="",
            appwrite_accounts_table="ssfx_accounts",
            appwrite_presets_table="ssfx_presets",
            appwrite_executions_table="ssfx_executions",
            appwrite_risk_state_table="ssfx_risk_state",
            dataservice_base_url="",
            dataservice_api_key="",
            agent_harness_base_url="",
            agent_intent_enabled=False,
            ctrader_broker_url="",
            admin_site_origin="",
            admin_api_key=admin_api_key,
            signal_experience_enabled=False,
            signal_experience_database_id="",
            signal_experience_block_threshold=0.5,
            signal_experience_reduce_threshold=0.75,
            agent_autonomy_enabled=False,
            gold_quant_signal_enabled=False,
            gold_quant_signal_interval_sec=60.0,
            gold_quant_min_confidence=0.75,
            gold_quant_agent_min_confidence=0.65,
        )
        self.followers = {}


def _make_request(admin_key: str = "") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"x-admin-key", admin_key.encode())],
        "path": "/api/signals/inject",
        "query_string": b"",
    }
    return Request(scope)


def test_server_config_requires_webhook_secret_in_webhook_mode():
    cfg = _FakeState().config
    cfg.require_webhook_secret()  # secret is set; should not raise


def test_server_config_requires_webhook_secret_missing_raises():
    cfg = _FakeState().config
    cfg.telegram_webhook_secret_token = ""
    with pytest.raises(ValueError, match="TELEGRAM_WEBHOOK_SECRET_TOKEN"):
        cfg.require_webhook_secret()


def test_server_config_skips_webhook_secret_in_polling_mode():
    cfg = _FakeState().config
    cfg.webhook_host = "polling"
    cfg.telegram_webhook_secret_token = ""
    cfg.require_webhook_secret()  # should not raise


def test_admin_api_key_required():
    admin_api.set_state(_FakeState(admin_api_key="secret"))
    req = _make_request(admin_key="secret")
    admin_api._require_admin_key(req)  # should not raise


def test_admin_api_key_missing_rejected():
    admin_api.set_state(_FakeState(admin_api_key=""))
    req = _make_request(admin_key="")
    with pytest.raises(HTTPException) as exc_info:
        admin_api._require_admin_key(req)
    assert exc_info.value.status_code == 503


def test_admin_api_key_wrong_rejected():
    admin_api.set_state(_FakeState(admin_api_key="secret"))
    req = _make_request(admin_key="wrong")
    with pytest.raises(HTTPException) as exc_info:
        admin_api._require_admin_key(req)
    assert exc_info.value.status_code == 401
