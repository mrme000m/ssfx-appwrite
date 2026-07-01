"""Load per-user/slave trading config from Appwrite TablesDB."""
from __future__ import annotations

import json
import logging
from typing import Any

from appwrite.client import Client
from appwrite.services.tables_db import TablesDB

from ssfx_trader.config import AccountConfig, CTraderConfig, PerAccountTradingConfig

logger = logging.getLogger(__name__)


class UserConfigStore:
    """Reads `ssfx_accounts` rows and maps them to AccountConfig."""

    def __init__(self, client: Client, database_id: str, accounts_table: str):
        self._tables = TablesDB(client)
        self._database_id = database_id
        self._accounts_table = accounts_table

    def load(self, name: str) -> AccountConfig | None:
        try:
            row = self._tables.get_row(
                database_id=self._database_id,
                table_id=self._accounts_table,
                row_id=name,
            )
            data = row.model_dump()
            return self._to_account_config(name, data)
        except Exception as exc:
            logger.warning("Could not load account config %s: %s", name, exc)
            return None

    def load_by_grant(self, grant_id: str, ctid: int) -> AccountConfig | None:
        """Find account row matching grant_id/ctid."""
        try:
            result = self._tables.list_rows(
                database_id=self._database_id,
                table_id=self._accounts_table,
                queries=[f'equal(\"grant_id\", \"{grant_id}\")'],
            )
            for row in result.rows:
                data = row.model_dump()
                cfg = self._to_account_config(row.id, data)
                if cfg.ctrader.grant_id == grant_id and cfg.ctrader.account_id == ctid:
                    return cfg
        except Exception as exc:
            logger.warning("Could not query account configs: %s", exc)
        return None

    def _to_account_config(self, name: str, data: dict[str, Any]) -> AccountConfig:
        # Support both legacy mongo-shaped docs and TablesDB rows with config_json
        if "config_json" in data:
            cfg = json.loads(data["config_json"] or "{}")
            data = {**data, **cfg}

        ct = data.get("ctrader", {})
        tr = data.get("trading", {})
        return AccountConfig(
            name=name or data.get("name", ""),
            enabled=data.get("enabled", True),
            ctrader=CTraderConfig(
                broker_url=ct.get("broker_url", ""),
                grant_id=ct.get("grant_id", ""),
                client_id=ct.get("client_id", ""),
                client_secret=ct.get("client_secret", ""),
                account_id=int(ct.get("account_id", 0)) or int(data.get("ctid_trader_account_id", 0)),
                host_type=ct.get("host_type", "demo"),
            ),
            trading=PerAccountTradingConfig(**tr) if isinstance(tr, dict) else PerAccountTradingConfig(),
            owner_id=data.get("owner_id", "system"),
            symbols_filter=data.get("symbols_filter", []),
        )
