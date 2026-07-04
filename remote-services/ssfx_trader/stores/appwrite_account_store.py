"""Appwrite TablesDB-backed account store."""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from appwrite.services.tables_db import TablesDB

from shared.appwrite_client import create_appwrite_client
from ssfx_parser import SlaveExecution
from ssfx_trader.config import AccountConfig

logger = logging.getLogger(__name__)

SYSTEM_OWNER_ID = "system"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _account_doc_to_appwrite(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert an AccountConfig-shaped dict into an Appwrite row."""
    name = doc.get("name") or doc.get("_id", "")
    ctrader = doc.get("ctrader", {})
    trading = doc.get("trading", {})
    config_json = json.dumps(
        {
            "ctrader": ctrader,
            "trading": trading,
            "symbols_filter": doc.get("symbols_filter", []),
        },
        separators=(",", ":"),
    )
    return {
        "name": name,
        "enabled": doc.get("enabled", True),
        "owner_id": doc.get("owner_id", SYSTEM_OWNER_ID),
        "host_type": ctrader.get("host_type", "demo"),
        "config_json": config_json,
        "updated_at": _now_iso(),
    }


def _appwrite_row_to_account_doc(row: dict[str, Any]) -> dict[str, Any]:
    """Convert an Appwrite row into an AccountConfig-shaped dict."""
    config = json.loads(row.get("config_json", "{}"))
    return {
        "_id": row["name"],
        "name": row["name"],
        "enabled": row.get("enabled", True),
        "owner_id": row.get("owner_id", SYSTEM_OWNER_ID),
        "ctrader": config.get("ctrader", {}),
        "trading": config.get("trading", {}),
        "symbols_filter": config.get("symbols_filter", []),
    }


class AppwriteAccountStore:
    """Loads and saves account configuration from Appwrite TablesDB."""

    def __init__(
        self,
        tables_db: TablesDB | None = None,
        database_id: str | None = None,
        table_id: str | None = None,
        executions_table_id: str | None = None,
        risk_state_table_id: str | None = None,
    ):
        if tables_db is None:
            _, tables_db = create_appwrite_client()
        self.tables_db = tables_db
        self.database_id = database_id or os.getenv("APPWRITE_DATABASE_ID", "slwp_platform")
        self.table_id = table_id or os.getenv("APPWRITE_ACCOUNTS_TABLE", "signal_slaves")
        self.executions_table_id = executions_table_id or os.getenv("APPWRITE_EXECUTIONS_TABLE", "ssfx_executions")
        self.risk_state_table_id = risk_state_table_id or os.getenv("APPWRITE_RISK_STATE_TABLE", "risk_state")

    def list_accounts(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        try:
            result = self.tables_db.list_rows(
                database_id=self.database_id,
                table_id=self.table_id,
            )
            rows = getattr(result, "rows", [])
            docs = [
                _appwrite_row_to_account_doc(getattr(row, "data", row))
                for row in rows
            ]
            if owner_id:
                docs = [
                    doc
                    for doc in docs
                    if doc.get("owner_id") == owner_id
                    or doc.get("owner_id") == SYSTEM_OWNER_ID
                ]
            return docs
        except Exception as exc:
            logger.error("Failed to list accounts from Appwrite: %s", exc)
            return []

    def get_account(self, name: str, owner_id: str | None = None) -> dict[str, Any] | None:
        try:
            row = self.tables_db.get_row(
                database_id=self.database_id,
                table_id=self.table_id,
                row_id=name,
            )
            data = getattr(row, "data", row)
            doc = _appwrite_row_to_account_doc(data)
            if owner_id and doc.get("owner_id") not in (owner_id, SYSTEM_OWNER_ID):
                return None
            return doc
        except Exception as exc:
            logger.error("Failed to get account %s from Appwrite: %s", name, exc)
            return None

    def load(self, name: str) -> AccountConfig | None:
        """Load an AccountConfig by name from Appwrite."""
        doc = self.get_account(name)
        if doc is None:
            return None
        try:
            return AccountConfig.from_doc(doc)
        except Exception as exc:
            logger.error("Failed to parse AccountConfig for %s: %s", name, exc)
            return None

    def load_by_grant(self, grant_id: str, ctid: int) -> AccountConfig | None:
        """Find account row matching grant_id/ctid."""
        try:
            result = self.tables_db.list_rows(
                database_id=self.database_id,
                table_id=self.table_id,
                queries=[f'equal("grant_id", "{grant_id}")'],
            )
            for row in result.rows:
                data = getattr(row, "data", row)
                doc = _appwrite_row_to_account_doc(data)
                cfg = AccountConfig.from_doc(doc)
                if cfg.ctrader.grant_id == grant_id and cfg.ctrader.account_id == ctid:
                    return cfg
        except Exception as exc:
            logger.warning("Could not query account configs for grant %s: %s", grant_id, exc)
        return None

    def save_account(self, account: Any) -> None:
        doc = account.to_doc() if hasattr(account, "to_doc") else dict(account)
        row = _account_doc_to_appwrite(doc)
        row_id = row["name"]
        try:
            self.tables_db.upsert_row(
                database_id=self.database_id,
                table_id=self.table_id,
                row_id=row_id,
                data=row,
            )
        except Exception as exc:
            logger.error("Failed to save account %s to Appwrite: %s", row_id, exc)
            raise

    def delete_account(self, name: str) -> None:
        try:
            self.tables_db.delete_row(
                database_id=self.database_id,
                table_id=self.table_id,
                row_id=name,
            )
        except Exception as exc:
            logger.error("Failed to delete account %s from Appwrite: %s", name, exc)
            raise

    def _execution_row_id(self, account_name: str, chat_id: str, message_id: int) -> str:
        return f"{account_name}:{chat_id}:{message_id}"

    def _execution_to_row(
        self,
        account_name: str,
        chat_id: str,
        message_id: int,
        signal_type: str,
        status: str,
        order_id: int | None,
        position_id: int | None,
        executed_price: float | None,
        volume: float | None,
        original_volume_lots: float | None,
        error: str | None,
        skip_reason: str | None,
    ) -> dict[str, Any]:
        return {
            "account_name": account_name,
            "chat_id": chat_id,
            "message_id": message_id,
            "signal_type": signal_type,
            "status": status,
            "order_id": order_id,
            "position_id": position_id,
            "executed_price": executed_price,
            "volume": volume,
            "original_volume_lots": original_volume_lots,
            "error": error,
            "skip_reason": skip_reason,
            "updated_at": _now_iso(),
        }

    def has_execution(self, slave_id: str, chat_id: str, message_id: int) -> bool:
        return self.get_execution(slave_id, chat_id, message_id) is not None

    def get_execution(
        self, slave_id: str, chat_id: str, message_id: int
    ) -> SlaveExecution | None:
        try:
            row = self.tables_db.get_row(
                database_id=self.database_id,
                table_id=self.executions_table_id,
                row_id=self._execution_row_id(slave_id, chat_id, message_id),
            )
            data = getattr(row, "data", row)
            return SlaveExecution(
                slave_id=data.get("account_name", slave_id),
                signal_chat_id=data.get("chat_id", chat_id),
                signal_message_id=int(data.get("message_id", message_id)),
                signal_type=data.get("signal_type", "NEW"),
                status=data.get("status", "unknown"),
                order_id=data.get("order_id"),
                position_id=data.get("position_id"),
                executed_price=data.get("executed_price"),
                volume=data.get("volume"),
                original_volume_lots=data.get("original_volume_lots"),
                error=data.get("error"),
                skip_reason=data.get("skip_reason"),
            )
        except Exception as exc:
            logger.error("Failed to get execution %s:%s:%s: %s", slave_id, chat_id, message_id, exc)
            return None

    def update_execution(
        self,
        slave_id: str,
        chat_id: str,
        message_id: int,
        signal_type: str,
        status: str,
        order_id: int | None = None,
        position_id: int | None = None,
        executed_price: float | None = None,
        volume: float | None = None,
        original_volume_lots: float | None = None,
        error: str | None = None,
        skip_reason: str | None = None,
    ) -> None:
        row = self._execution_to_row(
            account_name=slave_id,
            chat_id=chat_id,
            message_id=message_id,
            signal_type=signal_type,
            status=status,
            order_id=order_id,
            position_id=position_id,
            executed_price=executed_price,
            volume=volume,
            original_volume_lots=original_volume_lots,
            error=error,
            skip_reason=skip_reason,
        )
        try:
            self.tables_db.upsert_row(
                database_id=self.database_id,
                table_id=self.executions_table_id,
                row_id=self._execution_row_id(slave_id, chat_id, message_id),
                data=row,
            )
        except Exception as exc:
            logger.error("Failed to update execution %s:%s:%s: %s", slave_id, chat_id, message_id, exc)
            raise

    def mark_skipped(self, slave_id: str, chat_id: str, message_id: int, reason: str) -> None:
        self.update_execution(
            slave_id=slave_id,
            chat_id=chat_id,
            message_id=message_id,
            signal_type="NEW",
            status="skipped",
            skip_reason=reason,
        )

    def get_active_executions(self, slave_id: str) -> list[SlaveExecution]:
        try:
            result = self.tables_db.list_rows(
                database_id=self.database_id,
                table_id=self.executions_table_id,
            )
            rows = getattr(result, "rows", [])
            executions: list[SlaveExecution] = []
            for row in rows:
                data = getattr(row, "data", row)
                if data.get("account_name") != slave_id:
                    continue
                if data.get("status") != "executed":
                    continue
                executions.append(
                    SlaveExecution(
                        slave_id=data.get("account_name", slave_id),
                        signal_chat_id=data.get("chat_id", ""),
                        signal_message_id=int(data.get("message_id", 0)),
                        signal_type=data.get("signal_type", "NEW"),
                        status=data.get("status", "unknown"),
                        order_id=data.get("order_id"),
                        position_id=data.get("position_id"),
                        executed_price=data.get("executed_price"),
                        volume=data.get("volume"),
                        original_volume_lots=data.get("original_volume_lots"),
                        error=data.get("error"),
                        skip_reason=data.get("skip_reason"),
                    )
                )
            return executions
        except Exception as exc:
            logger.error("Failed to list active executions for %s: %s", slave_id, exc)
            return []

    def list_recent_executions(self, slave_id: str, limit: int = 50) -> list[SlaveExecution]:
        try:
            result = self.tables_db.list_rows(
                database_id=self.database_id,
                table_id=self.executions_table_id,
            )
            rows = getattr(result, "rows", [])
            executions: list[SlaveExecution] = []
            for row in rows:
                data = getattr(row, "data", row)
                if data.get("account_name") != slave_id:
                    continue
                executions.append(
                    SlaveExecution(
                        slave_id=data.get("account_name", slave_id),
                        signal_chat_id=data.get("chat_id", ""),
                        signal_message_id=int(data.get("message_id", 0)),
                        signal_type=data.get("signal_type", "NEW"),
                        status=data.get("status", "unknown"),
                        order_id=data.get("order_id"),
                        position_id=data.get("position_id"),
                        executed_price=data.get("executed_price"),
                        volume=data.get("volume"),
                        original_volume_lots=data.get("original_volume_lots"),
                        error=data.get("error"),
                        skip_reason=data.get("skip_reason"),
                    )
                )
            executions.sort(key=lambda e: e.updated_at or e.created_at, reverse=True)
            return executions[:limit]
        except Exception as exc:
            logger.error("Failed to list recent executions for %s: %s", slave_id, exc)
            return []

    def _risk_state_row_id(self, account_name: str, date_str: str) -> str:
        return f"{account_name}:{date_str}"

    def get_risk_state(self, account_name: str, date_str: str) -> dict[str, Any] | None:
        try:
            row = self.tables_db.get_row(
                database_id=self.database_id,
                table_id=self.risk_state_table_id,
                row_id=self._risk_state_row_id(account_name, date_str),
            )
            data = getattr(row, "data", row)
            return json.loads(data.get("state_json", "{}"))
        except Exception as exc:
            logger.warning("Failed to get risk state for %s:%s: %s", account_name, date_str, exc)
            return None

    def upsert_risk_state(self, account_name: str, state: dict[str, Any]) -> None:
        date_str = state.get("date_str", "")
        row_id = self._risk_state_row_id(account_name, date_str)
        row = {
            "account_name": account_name,
            "date_str": date_str,
            "daily_start_equity": state.get("daily_start_equity"),
            "daily_pnl": state.get("daily_pnl"),
            "peak_equity": state.get("peak_equity"),
            "kill_switch_active": state.get("kill_switch_active"),
            "kill_switch_reason": state.get("kill_switch_reason"),
            "state_json": json.dumps(state, separators=(",", ":")),
            "updated_at": _now_iso(),
        }
        try:
            self.tables_db.upsert_row(
                database_id=self.database_id,
                table_id=self.risk_state_table_id,
                row_id=row_id,
                data=row,
            )
        except Exception as exc:
            logger.error("Failed to upsert risk state for %s:%s: %s", account_name, date_str, exc)
            raise
