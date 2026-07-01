"""Trading execution HTTP endpoints."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from ctrader.config import CTRADERConfig

from .models import SignalRequest
from .signal_router import SignalRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trade", tags=["trade"])


def _get_config(request: Request) -> CTRADERConfig:
    return request.app.state.config


def _get_signal_router(request: Request) -> SignalRouter:
    return request.app.state.signal_router


def require_auth(
    request: Request,
    x_slave_key: str | None = Header(None),
    x_admin_key: str | None = Header(None),
) -> None:
    config: CTRADERConfig = request.app.state.config
    if x_admin_key and x_admin_key == config.admin_api_key:
        return
    if x_slave_key and x_slave_key == config.slave_api_key:
        return
    raise HTTPException(status_code=401, detail="Missing or invalid auth key")


@router.get("/health")
async def trade_health() -> dict[str, str]:
    return {"status": "ok", "service": "ctrader-trading"}


@router.post("/{grant_id}/account/{ctid}/signals")
async def trade_signal(
    grant_id: str,
    ctid: int,
    body: dict[str, Any],
    signal_router: SignalRouter = Depends(_get_signal_router),
    _auth: None = Depends(require_auth),
) -> JSONResponse:
    request = SignalRequest.from_dict({**body, "grant_id": grant_id, "ctid_trader_account_id": ctid})
    response = await signal_router.route(request)
    return JSONResponse(content=response.to_dict())


@router.post("/{grant_id}/account/{ctid}/signals/{signal_type}")
async def trade_signal_simple(
    grant_id: str,
    ctid: int,
    signal_type: str,
    body: dict[str, Any],
    signal_router: SignalRouter = Depends(_get_signal_router),
    _auth: None = Depends(require_auth),
) -> JSONResponse:
    request = SignalRequest.from_dict(
        {**body, "grant_id": grant_id, "ctid_trader_account_id": ctid, "signal_type": signal_type}
    )
    response = await signal_router.route(request)
    return JSONResponse(content=response.to_dict())
