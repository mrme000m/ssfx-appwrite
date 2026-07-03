"""Route incoming signal requests to the correct ExecutorWorker."""
from __future__ import annotations

import logging

from ssfx_trader.config import AccountConfig
from ssfx_trader.stores.appwrite_account_store import AppwriteAccountStore

from .auth import TokenClient
from .event_relay import EventRelay
from .executor_worker import ExecutorWorker
from .models import ExecutionResponse, SignalRequest
from .session_manager import SessionManager

logger = logging.getLogger(__name__)


class SignalRouter:
    """Manages one ExecutorWorker per (grant_id, ctid)."""

    def __init__(
        self,
        token_client: TokenClient,
        session_manager: SessionManager,
        event_relay: EventRelay,
        config_store: AppwriteAccountStore,
        data_service_base_url: str = "",
        data_service_api_key: str = "",
    ):
        self._token_client = token_client
        self._session_manager = session_manager
        self._event_relay = event_relay
        self._config_store = config_store
        self._data_service_base_url = data_service_base_url
        self._data_service_api_key = data_service_api_key
        self._workers: dict[tuple[str, int], ExecutorWorker] = {}

    async def route(self, request: SignalRequest) -> ExecutionResponse:
        key = (request.grant_id, request.ctid_trader_account_id)
        worker = self._workers.get(key)
        if worker is None:
            cfg = self._config_store.load_by_grant(request.grant_id, request.ctid_trader_account_id)
            if cfg is None:
                cfg = self._default_config(request)
            worker = ExecutorWorker(
                account_config=cfg,
                session_manager=self._session_manager,
                event_relay=self._event_relay,
                data_service_base_url=self._data_service_base_url,
                data_service_api_key=self._data_service_api_key,
            )
            self._workers[key] = worker
        return await worker.execute(request)

    def _default_config(self, request: SignalRequest) -> AccountConfig:
        from ssfx_trader.config import CTraderConfig, PerAccountTradingConfig

        return AccountConfig(
            name=f"{request.grant_id}:{request.ctid_trader_account_id}",
            enabled=True,
            ctrader=CTraderConfig(
                broker_url="",
                grant_id=request.grant_id,
                account_id=request.ctid_trader_account_id,
                host_type="demo",
            ),
            trading=PerAccountTradingConfig(),
        )

    async def close_all(self) -> None:
        await self._session_manager.close_all()
        self._workers.clear()
