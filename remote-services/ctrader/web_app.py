"""Unified ctrader service — market data + trading execution."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from appwrite.client import Client
from fastapi import FastAPI

from ctrader.config import CTRADERConfig
from ctrader.trading import router as trade_router
from ctrader.trading.auth import TokenClient
from ctrader.trading.event_relay import EventRelay
from ctrader.trading.session_manager import SessionManager
from ctrader.trading.signal_router import SignalRouter
from ctrader.trading.user_config_store import UserConfigStore

logger = logging.getLogger(__name__)

config = CTRADERConfig.from_env()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.require_appwrite()
    config.require_internal_key()

    logging.basicConfig(level=getattr(logging, config.log_level.upper(), logging.INFO))

    appwrite_client = Client()
    appwrite_client.set_endpoint(config.appwrite_endpoint)
    appwrite_client.set_project(config.appwrite_project_id)
    appwrite_client.set_key(config.appwrite_api_key)

    # ── Trading execution ───────────────────────────────────────────────────
    token_client = TokenClient(
        broker_url=config.ctrader_auth_broker_url,
        internal_api_key=config.internal_api_key,
    )
    session_manager = SessionManager(
        token_client=token_client,
        broker_url=config.ctrader_auth_broker_url,
        client_id=config.ctrader_client_id,
        client_secret=config.ctrader_client_secret,
    )
    config_store = UserConfigStore(
        client=appwrite_client,
        database_id=config.appwrite_database_id,
        accounts_table=config.accounts_table,
    )
    event_relay = EventRelay(
        client=appwrite_client,
        database_id=config.appwrite_database_id,
        events_table=config.events_table,
    )
    signal_router = SignalRouter(
        token_client=token_client,
        session_manager=session_manager,
        event_relay=event_relay,
        config_store=config_store,
        data_service_base_url=config.data_service_url,
        data_service_api_key=config.data_service_api_key,
    )

    app.state.config = config
    app.state.signal_router = signal_router

    logger.info("ctrader service started on %s:%s", config.host, config.port)

    yield

    # ── Shutdown ────────────────────────────────────────────────────────────
    logger.info("Shutting down ctrader service")
    await signal_router.close_all()
    await session_manager.close_all()




app = FastAPI(title="ctrader", lifespan=lifespan)
app.include_router(trade_router.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "ctrader"}
