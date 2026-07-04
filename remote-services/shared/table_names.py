"""Canonical table and database names for the slwp platform.

Centralizes naming so renames only require changes in one place.
During cutover, legacy aliases are read from environment variables when available.
"""
from __future__ import annotations

import os

LEGACY_DB = "ctrader_auth"
PLATFORM_DB = "slwp_platform"

# Allow APPWRITE_DATABASE_ID to override during cutover
DATABASE_ID = os.getenv("APPWRITE_DATABASE_ID", os.getenv("CTRADER_AUTH_DATABASE_ID", PLATFORM_DB))

# Table name constants (canonical → legacy fallback)
USERS_TABLE = os.getenv("APPWRITE_USERS_TABLE", "users")
CTRADER_ACCOUNTS_TABLE = os.getenv("APPWRITE_CTRADER_ACCOUNTS_TABLE", "ctrader_accounts")
TRADE_SETTINGS_TABLE = os.getenv("APPWRITE_TRADE_SETTINGS_TABLE", "trade_settings")
SIGNAL_SLAVES_TABLE = os.getenv("APPWRITE_SIGNAL_SLAVES_TABLE", "signal_slaves")
ACCOUNT_STATE_HISTORY_TABLE = os.getenv("APPWRITE_ACCOUNT_STATE_TABLE", "account_state_history")
SIGNAL_BROADCASTS_TABLE = os.getenv("APPWRITE_SIGNAL_BROADCASTS_TABLE", "signal_broadcasts")
SIGNAL_EXECUTIONS_TABLE = os.getenv("APPWRITE_EXECUTIONS_TABLE", "ssfx_executions")
TRADING_EVENTS_TABLE = os.getenv("APPWRITE_TRADING_EVENTS_TABLE", "trading_events")
EPHEMERAL_TOKENS_TABLE = os.getenv("APPWRITE_EPHEMERAL_TOKENS_TABLE", "ephemeral_tokens")
GRANT_LOCKS_TABLE = os.getenv("APPWRITE_GRANT_LOCKS_TABLE", "grant_locks")
SERVICE_CONFIG_TABLE = os.getenv("APPWRITE_SERVICE_CONFIG_TABLE", "service_config")
PRESETS_TABLE = os.getenv("APPWRITE_PRESETS_TABLE", "ssfx_presets")
RISK_STATE_TABLE = os.getenv("APPWRITE_RISK_STATE_TABLE", "ssfx_risk_state")

# Convenience dict for iteration
ALL_TABLES: dict[str, str] = {
    "users": USERS_TABLE,
    "ctrader_accounts": CTRADER_ACCOUNTS_TABLE,
    "trade_settings": TRADE_SETTINGS_TABLE,
    "signal_slaves": SIGNAL_SLAVES_TABLE,
    "account_state_history": ACCOUNT_STATE_HISTORY_TABLE,
    "signal_broadcasts": SIGNAL_BROADCASTS_TABLE,
    "signal_executions": SIGNAL_EXECUTIONS_TABLE,
    "trading_events": TRADING_EVENTS_TABLE,
    "ephemeral_tokens": EPHEMERAL_TOKENS_TABLE,
    "grant_locks": GRANT_LOCKS_TABLE,
    "service_config": SERVICE_CONFIG_TABLE,
    "presets": PRESETS_TABLE,
    "risk_state": RISK_STATE_TABLE,
}
