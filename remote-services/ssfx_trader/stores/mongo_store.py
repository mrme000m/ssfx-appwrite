"""MongoDB-backed store for signals and account executions."""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection

from ssfx_parser import FollowerExecution, RawMessage, SignalStatus, TradeSignal

logger = logging.getLogger(__name__)


class MongoSignalStore:
    """Global signal and raw-message store."""

    def __init__(
        self,
        mongo_uri: str,
        database: str,
        client: MongoClient | None = None,
    ):
        self._client: MongoClient = client or MongoClient(mongo_uri)
        self._db = self._client[database]
        self._signals: Collection = self._db["ssfx_signals"]
        self._raw_messages: Collection = self._db["ssfx_raw_messages"]
        self._trades: Collection = self._db["ssfx_trades"]
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        self._raw_messages.create_index([("chat_id", ASCENDING), ("message_id", ASCENDING)], unique=True)
        self._signals.create_index([("chat_id", ASCENDING), ("message_id", ASCENDING)], unique=True)
        self._signals.create_index([("status", ASCENDING), ("signal_type", ASCENDING)])
        self._signals.create_index([("timestamp_ms", ASCENDING)])

    def save_raw_message(self, msg: RawMessage) -> bool:
        doc = msg.to_mongo()
        key = {"chat_id": msg.chat_id, "message_id": msg.message_id}
        result = self._raw_messages.update_one(key, {"$set": doc}, upsert=True)
        return result.upserted_id is not None

    def get_today_messages(self, chat_id: str | None = None) -> list[RawMessage]:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        query: dict[str, Any] = {"date": today}
        if chat_id:
            query["chat_id"] = chat_id
        docs = self._raw_messages.find(query).sort("timestamp_ms", ASCENDING)
        return [RawMessage.from_mongo(doc) for doc in docs]

    def save_signal(self, signal: TradeSignal) -> bool:
        doc = signal.to_mongo()
        doc["status"] = SignalStatus.EMITTED.value
        key = {"chat_id": signal.chat_id, "message_id": signal.message_id}
        result = self._signals.update_one(key, {"$set": doc}, upsert=True)
        return result.upserted_id is not None

    def get_signal(self, chat_id: str, message_id: int) -> TradeSignal | None:
        doc = self._signals.find_one({"chat_id": chat_id, "message_id": message_id})
        return TradeSignal.from_mongo(doc) if doc else None

    def get_active_signals(self, chat_id: str | None = None) -> list[TradeSignal]:
        query: dict[str, Any] = {"status": SignalStatus.EXECUTED.value}
        if chat_id:
            query["chat_id"] = chat_id
        docs = self._signals.find(query).sort("timestamp_ms", ASCENDING)
        return [TradeSignal.from_mongo(doc) for doc in docs]

    def get_pending_entry_signals(self) -> list[TradeSignal]:
        docs = self._signals.find(
            {"signal_type": "NEW", "status": SignalStatus.EMITTED.value}
        ).sort("timestamp_ms", ASCENDING)
        return [TradeSignal.from_mongo(doc) for doc in docs]

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
        update: dict[str, Any] = {"status": status}
        if order_id is not None:
            update["order_id"] = order_id
        if position_id is not None:
            update["position_id"] = position_id
        if executed_price is not None:
            update["executed_price"] = executed_price
        if error is not None:
            update["error"] = error
        if volume is not None:
            update["volume"] = volume
        self._signals.update_one(
            {"chat_id": chat_id, "message_id": message_id},
            {"$set": update},
        )

    def update_signal_entry(self, chat_id: str, message_id: int, entry_price: float) -> None:
        self._signals.update_one(
            {"chat_id": chat_id, "message_id": message_id},
            {"$set": {"entry_price": entry_price}},
        )

    def record_trade(self, signal: TradeSignal, result: dict[str, Any]) -> None:
        self._trades.update_one(
            {"signal_key": f"{signal.chat_id}:{signal.message_id}"},
            {
                "$set": {
                    "signal_key": f"{signal.chat_id}:{signal.message_id}",
                    "signal": signal.model_dump(mode="json"),
                    "result": result,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            },
            upsert=True,
        )

    def list_signals(self, limit: int = 50) -> list[TradeSignal]:
        docs = self._signals.find().sort("timestamp_ms", DESCENDING).limit(limit)
        return [TradeSignal.from_mongo(doc) for doc in docs]

    def list_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        docs = self._trades.find().sort("updated_at", DESCENDING).limit(limit)
        return list(docs)

    async def watch_signals(self) -> AsyncIterator[TradeSignal]:
        pipeline = [{"$match": {"operationType": "insert"}}]
        try:
            with self._signals.watch(pipeline, full_document="updateLookup") as stream:
                for change in stream:
                    doc = change.get("fullDocument")
                    if doc:
                        yield TradeSignal.from_mongo(doc)
        except Exception as exc:
            logger.error("Change stream error: %s", exc)
            raise


class MongoAccountStore:
    """Per-account execution state store."""

    def __init__(
        self,
        mongo_uri: str,
        database: str,
        follower_id: str,
        client: MongoClient | None = None,
    ):
        self._client: MongoClient = client or MongoClient(mongo_uri)
        self._db = self._client[database]
        self._follower_id = follower_id
        self._accounts: Collection = self._db["ssfx_accounts"]
        self._executions: Collection = self._db["ssfx_follower_executions"]
        self._risk_state: Collection = self._db["ssfx_risk_state"]
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        self._accounts.create_index("_id", unique=True)
        self._risk_state.create_index(
            [("account_name", ASCENDING), ("date_str", ASCENDING)], unique=True
        )
        self._executions.create_index(
            [("follower_id", ASCENDING), ("signal_chat_id", ASCENDING), ("signal_message_id", ASCENDING)],
            unique=True,
        )

    def list_accounts(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if owner_id:
            query["owner_id"] = owner_id
        return list(self._accounts.find(query))

    def get_account(self, name: str, owner_id: str | None = None) -> dict[str, Any] | None:
        query: dict[str, Any] = {"_id": name}
        if owner_id:
            query["owner_id"] = owner_id
        return self._accounts.find_one(query)

    def save_account(self, account: Any) -> None:
        doc = account.to_mongo() if hasattr(account, "to_mongo") else dict(account)
        self._accounts.update_one({"_id": doc["_id"]}, {"$set": doc}, upsert=True)

    def has_execution(self, follower_id: str, chat_id: str, message_id: int) -> bool:
        return self._executions.find_one(
            {
                "follower_id": follower_id,
                "signal_chat_id": chat_id,
                "signal_message_id": message_id,
            }
        ) is not None

    def get_execution(self, follower_id: str, chat_id: str, message_id: int) -> FollowerExecution | None:
        doc = self._executions.find_one(
            {
                "follower_id": follower_id,
                "signal_chat_id": chat_id,
                "signal_message_id": message_id,
            }
        )
        return FollowerExecution.from_mongo(doc) if doc else None

    def update_execution(
        self,
        follower_id: str,
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
        key = {
            "follower_id": follower_id,
            "signal_chat_id": chat_id,
            "signal_message_id": message_id,
        }
        update: dict[str, Any] = {
            "signal_type": signal_type,
            "status": status,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if order_id is not None:
            update["order_id"] = order_id
        if position_id is not None:
            update["position_id"] = position_id
        if executed_price is not None:
            update["executed_price"] = executed_price
        if volume is not None:
            update["volume"] = volume
        if original_volume_lots is not None:
            update["original_volume_lots"] = original_volume_lots
        if error is not None:
            update["error"] = error
        if skip_reason is not None:
            update["skip_reason"] = skip_reason

        result = self._executions.update_one(key, {"$set": update})
        if result.matched_count == 0:
            rec = FollowerExecution(
                follower_id=follower_id,
                signal_chat_id=chat_id,
                signal_message_id=message_id,
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
            self._executions.insert_one(rec.to_mongo())

    def mark_skipped(self, follower_id: str, chat_id: str, message_id: int, reason: str) -> None:
        self.update_execution(
            follower_id=follower_id,
            chat_id=chat_id,
            message_id=message_id,
            signal_type="NEW",
            status="skipped",
            skip_reason=reason,
        )

    def get_active_executions(self, follower_id: str) -> list[FollowerExecution]:
        docs = self._executions.find(
            {"follower_id": follower_id, "status": "executed"}
        ).sort("created_at", ASCENDING)
        return [FollowerExecution.from_mongo(doc) for doc in docs]

    def list_recent_executions(self, follower_id: str, limit: int = 50) -> list[FollowerExecution]:
        docs = (
            self._executions.find({"follower_id": follower_id})
            .sort("updated_at", DESCENDING)
            .limit(limit)
        )
        return [FollowerExecution.from_mongo(doc) for doc in docs]

    def _risk_state_id(self, account_name: str, date_str: str) -> str:
        return f"{account_name}:{date_str}"

    def get_risk_state(self, account_name: str, date_str: str) -> dict[str, Any] | None:
        try:
            doc = self._risk_state.find_one({"_id": self._risk_state_id(account_name, date_str)})
            if doc is None:
                return None
            return json.loads(doc.get("state_json", "{}"))
        except Exception as exc:
            logger.warning("Failed to get risk state for %s:%s: %s", account_name, date_str, exc)
            return None

    def upsert_risk_state(self, account_name: str, state: dict[str, Any]) -> None:
        date_str = state.get("date_str", "")
        row = {
            "_id": self._risk_state_id(account_name, date_str),
            "account_name": account_name,
            "date_str": date_str,
            "daily_start_equity": state.get("daily_start_equity"),
            "daily_pnl": state.get("daily_pnl"),
            "peak_equity": state.get("peak_equity"),
            "kill_switch_active": state.get("kill_switch_active"),
            "kill_switch_reason": state.get("kill_switch_reason"),
            "state_json": json.dumps(state, separators=(",", ":")),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        try:
            self._risk_state.update_one(
                {"_id": row["_id"]}, {"$set": row}, upsert=True
            )
        except Exception as exc:
            logger.error("Failed to upsert risk state for %s:%s: %s", account_name, date_str, exc)
            raise
