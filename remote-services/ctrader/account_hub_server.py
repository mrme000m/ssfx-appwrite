"""Account Hub WebSocket Server — main entry point.

Starts the AccountHub (persistent cTrader connections) and a FastAPI server
with WebSocket endpoints for real-time account data delivery.

Usage:
    python -m ctrader.account_hub_server
"""

from __future__ import annotations

import asyncio
import logging
import os

import uvicorn
from appwrite.client import Client

from ctrader.account_hub import AccountHub
from ctrader.config import CTRADERConfig
from ctrader.ws_server import AccountWebSocketServer


async def main() -> None:
    config = CTRADERConfig.from_env()
    config.require_appwrite()
    config.require_internal_key()

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("account-hub-server")
    logger.info("Starting Account Hub WS Server on port %s", config.account_hub_port)

    appwrite_client = Client()
    appwrite_client.set_endpoint(config.appwrite_endpoint)
    appwrite_client.set_project(config.appwrite_project_id)
    appwrite_client.set_key(config.appwrite_api_key)

    database_id = config.ctrader_auth_database_id or config.appwrite_database_id

    hub = AccountHub(
        appwrite_client=appwrite_client,
        database_id=database_id,
        slave_accounts_table=config.slave_accounts_table,
        internal_url=config.ctrader_auth_broker_url,
        internal_api_key=config.internal_api_key,
        client_id=config.ctrader_client_id,
        client_secret=config.ctrader_client_secret,
        poll_interval=config.account_hub_poll_interval,
        reconnect_base=config.account_hub_reconnect_base,
        reconnect_max=config.account_hub_reconnect_max,
    )

    await hub.start()

    server = AccountWebSocketServer(hub)

    config_uv = uvicorn.Config(
        app=server.app,
        host=config.host,
        port=config.account_hub_port,
        log_level=config.log_level.lower(),
    )
    uv_server = uvicorn.Server(config_uv)

    try:
        await uv_server.serve()
    finally:
        await hub.stop()


if __name__ == "__main__":
    asyncio.run(main())
