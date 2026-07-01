"""Unified ctrader service — market data + trading execution."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from appwrite.client import Client
from fastapi import FastAPI

from ctrader.config import CTRADERConfig
from ctrader.market import router as market_router
from ctrader.market.feed_client import DataServiceClient
from ctrader.trading import router as trade_router
from ctrader.trading.auth import TokenClient
from ctrader.trading.bad_trade_detector import BadTradeDetector
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

    # ── Market data feed ────────────────────────────────────────────────────
    feed_client = DataServiceClient(
        base_url=config.data_service_url,
        api_key=config.data_service_api_key,
        poll_interval_ms=config.data_poll_interval_ms,
    )
    await feed_client.__aenter__()
    market_router.feed_client = feed_client
    poll_task = asyncio.create_task(
        feed_client.poll_loop(market_router.event_queue),
        name="feed-poll-loop",
    )
    dispatch_task = asyncio.create_task(
        market_router.dispatch_events(),
        name="ws-dispatch-loop",
    )

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
    detector = BadTradeDetector(event_relay)
    event_relay.add_listener(detector.on_event)

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
    app.state.detector = detector

    logger.info("ctrader service started on %s:%s", config.host, config.port)

    yield

    # ── Shutdown ────────────────────────────────────────────────────────────
    logger.info("Shutting down ctrader service")
    await signal_router.close_all()
    await session_manager.close_all()

    dispatch_task.cancel()
    poll_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await dispatch_task
    with contextlib.suppress(asyncio.CancelledError):
        await poll_task

    await feed_client.__aexit__(None, None, None)


app = FastAPI(title="ctrader", lifespan=lifespan)
app.include_router(market_router.router)
app.include_router(trade_router.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "ctrader"}
