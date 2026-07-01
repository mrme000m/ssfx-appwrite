"""Market data REST and WebSocket endpoints."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from ctrader.config import CTRADERConfig

from .context_engine import ContextEngine
from .feed_client import DataServiceClient
from .hub import WebSocketHub

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/market", tags=["market"])

# Shared state wired by web_app.py
hub = WebSocketHub()
feed_client: DataServiceClient | None = None
context_engine = ContextEngine()
event_queue: asyncio.Queue[Any] = asyncio.Queue()


def get_config() -> CTRADERConfig:
    from ctrader.web_app import config
    return config


@router.get("/health")
async def market_health() -> dict[str, str]:
    return {"status": "ok", "service": "ctrader-data"}


@router.get("/{broker}/{symbol}/price")
async def price(broker: str, symbol: str, config: CTRADERConfig = Depends(get_config)) -> JSONResponse:
    if not feed_client:
        raise HTTPException(status_code=503, detail="Feed client not initialized")
    try:
        data = await feed_client.fetch_price(symbol)
        return JSONResponse(content=data)
    except Exception as exc:
        logger.warning("Price fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{broker}/{symbol}/context")
async def context(broker: str, symbol: str, config: CTRADERConfig = Depends(get_config)) -> JSONResponse:
    if not feed_client:
        raise HTTPException(status_code=503, detail="Feed client not initialized")
    try:
        snapshot = await feed_client.get_context_snapshot(symbol)
        return JSONResponse(content=snapshot.to_json())
    except Exception as exc:
        logger.warning("Context fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{broker}/{symbol}/prompt")
async def prompt(broker: str, symbol: str, config: CTRADERConfig = Depends(get_config)) -> JSONResponse:
    if not feed_client:
        raise HTTPException(status_code=503, detail="Feed client not initialized")
    try:
        snapshot = await feed_client.get_context_snapshot(symbol)
        text = context_engine.build_prompt(
            symbol,
            snapshot.price,
            snapshot.context,
            snapshot.quality,
        )
        return JSONResponse(content={"symbol": symbol.upper(), "prompt": text})
    except Exception as exc:
        logger.warning("Prompt build failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.websocket("/{broker}/{symbol}/ws")
async def market_ws(
    websocket: WebSocket,
    broker: str,
    symbol: str,
    token: str = Query(""),
    config: CTRADERConfig = Depends(get_config),
) -> None:
    # Minimal auth: validate bearer token or query token
    auth_header = websocket.headers.get("authorization", "")
    provided = token or (auth_header.replace("bearer ", "").replace("Bearer ", "") if auth_header else "")
    if config.data_api_secret and provided != config.data_api_secret:
        await websocket.close(code=1008, reason="Invalid token")
        return

    if not feed_client:
        await websocket.close(code=1011, reason="Feed unavailable")
        return

    await feed_client.subscribe(symbol)
    conn_id = await hub.connect(websocket)
    channel = f"spots:{symbol.upper()}"
    hub.subscribe(conn_id, [channel, f"context:{symbol.upper()}"])

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = __import__("json").loads(raw)
                action = msg.get("action")
                if action == "subscribe":
                    hub.subscribe(conn_id, msg.get("channels", []))
                elif action == "unsubscribe":
                    hub.unsubscribe(conn_id, msg.get("channels", []))
            except Exception as exc:
                logger.warning("Invalid WS message %s: %s", conn_id, exc)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(conn_id)
        await feed_client.unsubscribe(symbol)


async def dispatch_events() -> None:
    """Background task: route feed events to WebSocket hub channels."""
    while True:
        event = await event_queue.get()
        try:
            if hasattr(event, "symbol_name"):
                await hub.publish(f"spots:{event.symbol_name}", event)
            elif isinstance(event, dict) and event.get("symbol_name"):
                await hub.publish(f"spots:{event['symbol_name']}", event)
        except Exception as exc:
            logger.warning("Event dispatch error: %s", exc)
