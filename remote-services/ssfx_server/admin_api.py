"""Admin API surface for the cTrader AI Copy-Trading Command Center.

The router is mounted by ssfx_server.web_app and consumes the global
application state (slaves, signal_store, account_store, parser).
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from market_data_service.signal_experience.reporter import build_insights, build_llm_context
from ssfx_parser import Direction, SignalStatus, SignalType, TradeSignal
from ssfx_trader.config import AccountConfig as TraderAccountConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_app_state: Any | None = None


def set_state(state: Any) -> None:
    """Called by web_app lifespan after AppState is initialized."""
    global _app_state
    _app_state = state


def _state() -> Any:
    if _app_state is None:
        raise HTTPException(status_code=503, detail="server not initialized")
    return _app_state


def _require_admin_key(request: Request) -> None:
    cfg = _state().config
    if not cfg.admin_api_key:
        raise HTTPException(status_code=503, detail="admin API key not configured")
    
    # Check header first (preferred for API calls)
    provided = request.headers.get("x-admin-key", "")
    
    # Fall back to query parameter for SSE streams (browser compatibility)
    if not provided:
        provided = request.query_params.get("admin_key", "")
    
    if not secrets.compare_digest(provided, cfg.admin_api_key):
        raise HTTPException(status_code=401, detail="unauthorized")


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _serialize_signal(signal: TradeSignal) -> dict[str, Any]:
    return {
        "chat_id": signal.chat_id,
        "message_id": signal.message_id,
        "reply_to_message_id": signal.reply_to_message_id,
        "timestamp_ms": signal.timestamp_ms,
        "raw_text": signal.raw_text,
        "direction": signal.direction.value if signal.direction else None,
        "symbol": signal.symbol,
        "signal_type": signal.signal_type.value,
        "order_type": signal.order_type.value if signal.order_type else None,
        "entry_price": signal.entry_price,
        "tp1": signal.tp1,
        "tp2": signal.tp2,
        "tp3": signal.tp3,
        "sl": signal.sl,
        "profit_pips": signal.profit_pips,
        "close_percentage": signal.close_percentage,
        "tp_hit_number": signal.tp_hit_number,
        "parse_confidence": signal.parse_confidence,
        "parser_used": signal.parser_used,
        "llm_reasoning": signal.llm_reasoning,
        "quality_score": signal.quality_score,
        "quality_factors": signal.quality_factors,
        "experience_action": signal.experience_action,
        "volume_multiplier": signal.volume_multiplier,
        "status": signal.status.value,
        "order_id": signal.order_id,
        "position_id": signal.position_id,
        "executed_price": signal.executed_price,
        "error": signal.error,
    }


def _serialize_execution(exec_: Any) -> dict[str, Any]:
    return {
        "slave_id": exec_.slave_id,
        "signal_chat_id": exec_.signal_chat_id,
        "signal_message_id": exec_.signal_message_id,
        "signal_type": exec_.signal_type,
        "status": exec_.status,
        "order_id": exec_.order_id,
        "position_id": exec_.position_id,
        "executed_price": exec_.executed_price,
        "volume": exec_.volume,
        "original_volume_lots": exec_.original_volume_lots,
        "error": exec_.error,
        "skip_reason": exec_.skip_reason,
        "created_at": exec_.created_at,
        "updated_at": exec_.updated_at,
    }


class InjectSignalRequest(BaseModel):
    raw_text: str = ""
    symbol: str
    direction: str  # BUY or SELL
    signal_type: str = "NEW"
    entry_price: float | None = None
    sl: float | str | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    order_type: str | None = None
    chat_id: str = "manual"
    message_id: int | None = None
    reply_to_message_id: int | None = None
    parse_confidence: float = 1.0
    parser_used: str = "manual"


@router.get("/accounts")
async def list_accounts(request: Request) -> JSONResponse:
    _require_admin_key(request)
    state = _state()
    accounts = state.account_store.list_accounts()
    results = []
    for doc in accounts:
        name = doc.get("name", "")
        get_slave = state.slaves.get(name)
        runtime = {"running": False, "connected": False, "active_positions": 0}
        if slave is not None:
            task = getattr(slave, "_watch_task", None)
            runtime["running"] = task is not None and not task.done()
            backend = getattr(slave._executor, "_backend", None)
            runtime["connected"] = getattr(backend, "_connected", True)
            runtime["active_positions"] = slave._executor.active_position_count
        config = copy.deepcopy(doc)
        ctrader = config.get("ctrader", {})
        if isinstance(ctrader, dict):
            ctrader["client_secret"] = "***"
            ctrader["client_id"] = "***"
        results.append({"name": name, "enabled": doc.get("enabled", True), "host_type": doc.get("ctrader", {}).get("host_type", "demo"), "runtime": runtime, "config": config})
    return JSONResponse(results)


@router.get("/accounts/{name}/state")
async def account_state(name: str, request: Request) -> JSONResponse:
    _require_admin_key(request)
    state = _state()
    slave = state.slaves.get(name)
    if slave is None:
        raise HTTPException(status_code=404, detail=f"account {name} not found")

    executor = slave._executor
    backend = executor._backend
    connected = getattr(backend, "_connected", True)
    summary = {"balance": None, "equity": None}
    if connected:
        try:
            summary = await backend.get_account_summary()
        except Exception as exc:
            logger.warning("[%s] Failed to fetch account summary: %s", name, exc)

    positions = []
    for key, pos in executor._active_positions.items():
        signal = pos.get("signal")
        positions.append(
            {
                "key": key,
                "symbol": signal.symbol if signal else None,
                "direction": signal.direction.value if signal and signal.direction else None,
                "volume_lots": pos.get("volume_lots"),
                "remaining_volume_lots": pos.get("remaining_volume_lots"),
                "order_id": pos.get("order_id"),
                "position_id": pos.get("position_id"),
            }
        )

    recent = state.account_store.list_recent_executions(name, limit=20)
    last_error = None
    for ex in recent:
        if ex.status in ("failed", "rejected", "skipped") and (ex.error or ex.skip_reason):
            last_error = ex.error or ex.skip_reason
            break

    return JSONResponse(
        {
            "name": name,
            "running": getattr(slave, "_watch_task", None) is not None and not slave._watch_task.done(),
            "connected": connected,
            "summary": summary,
            "active_positions": executor.active_position_count,
            "positions": positions,
            "last_error": last_error,
            "recent_executions": [_serialize_execution(ex) for ex in recent],
        }
    )


@router.patch("/accounts/{name}")
async def update_account(name: str, request: Request) -> JSONResponse:
    _require_admin_key(request)
    state = _state()
    existing = state.account_store.get_account(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"account {name} not found")

    patch = await request.json()

    if "enabled" in patch:
        existing["enabled"] = bool(patch["enabled"])

    if "config" in patch:
        # Merge the patch on top of the existing document and validate through
        # the real AccountConfig dataclass, then re-serialize it for storage.
        merged = {**existing, **patch["config"]}
        merged.pop("_id", None)
        try:
            validated_config = TraderAccountConfig.from_doc(merged)
            serialized = validated_config.to_doc()
        except (ValueError, TypeError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid account config: {e}",
            ) from e
        existing["ctrader"] = serialized["ctrader"]
        existing["trading"] = serialized["trading"]
        existing["symbols_filter"] = serialized["symbols_filter"]

    state.account_store.save_account(existing)
    return JSONResponse({"ok": True, "name": name, "enabled": existing.get("enabled")})


@router.get("/signals")
async def list_signals(request: Request, limit: int = 50) -> JSONResponse:
    _require_admin_key(request)
    state = _state()
    signals = state.signal_store.list_signals(limit=limit)
    return JSONResponse([_serialize_signal(s) for s in signals])


@router.get("/signals/{chat_id}/{message_id}/executions")
async def signal_executions(chat_id: str, message_id: int, request: Request) -> JSONResponse:
    _require_admin_key(request)
    state = _state()
    results = []
    for name in state.slaves:
        exec_ = state.account_store.get_execution(name, chat_id, message_id)
        if exec_ is not None:
            results.append(_serialize_execution(exec_))
    return JSONResponse(results)


@router.post("/signals/inject")
async def inject_signal(payload: InjectSignalRequest, request: Request) -> JSONResponse:
    _require_admin_key(request)
    state = _state()

    try:
        direction = Direction(payload.direction.upper())
        signal_type = SignalType(payload.signal_type.upper())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid enum value: {exc}") from exc

    message_id = payload.message_id or _now_ms()
    from ssfx_parser import RawMessage

    raw = RawMessage(
        chat_id=payload.chat_id,
        message_id=message_id,
        text=payload.raw_text or f"MANUAL {payload.direction} {payload.symbol} @ {payload.entry_price}",
        reply_to_message_id=payload.reply_to_message_id,
        timestamp_ms=_now_ms(),
    )
    state.signal_store.save_raw_message(raw)

    signal = TradeSignal(
        raw_text=raw.text,
        direction=direction,
        symbol=payload.symbol.upper(),
        signal_type=signal_type,
        entry_price=payload.entry_price,
        sl=payload.sl,
        tp1=payload.tp1,
        tp2=payload.tp2,
        tp3=payload.tp3,
        parse_confidence=payload.parse_confidence,
        parser_used=payload.parser_used,
        message_id=message_id,
        chat_id=payload.chat_id,
        reply_to_message_id=payload.reply_to_message_id,
        timestamp_ms=raw.timestamp_ms,
        status=SignalStatus.EMITTED,
    )
    state.signal_store.save_signal(signal)

    logger.info(
        "Manual signal injected: %s %s %s entry=%s by admin",
        signal.signal_type.value,
        signal.direction.value,
        signal.symbol,
        signal.entry_price,
    )

    for slave in state.slaves.values():
        await slave.on_signal(signal)

    return JSONResponse({"ok": True, "signal": _serialize_signal(signal)})


@router.get("/executions")
async def list_executions(request: Request, limit: int = 50) -> JSONResponse:
    _require_admin_key(request)
    state = _state()
    results = []
    for name in state.slaves:
        for ex in state.account_store.list_recent_executions(name, limit=limit):
            results.append(_serialize_execution(ex))
    results.sort(key=lambda e: e.get("updated_at") or e.get("created_at", ""), reverse=True)
    return JSONResponse(results[:limit])


@router.get("/executions/stream")
async def execution_stream(request: Request) -> StreamingResponse:
    _require_admin_key(request)
    state = _state()

    async def event_generator():
        seen_trade_keys: set[str] = set()
        while True:
            await asyncio.sleep(2)
            try:
                trades = state.signal_store.list_trades(limit=100)
                for trade in trades:
                    key = trade.get("signal_key", "")
                    updated = trade.get("updated_at", "")
                    unique = f"{key}:{updated}"
                    if unique in seen_trade_keys:
                        continue
                    seen_trade_keys.add(unique)
                    # prune set to avoid unbounded growth
                    if len(seen_trade_keys) > 500:
                        seen_trade_keys.clear()
                    payload = {
                        "type": "trade",
                        "signal_key": key,
                        "signal": trade.get("signal"),
                        "result": trade.get("result"),
                        "updated_at": updated,
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
            except Exception as exc:
                logger.warning("execution_stream error: %s", exc)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/signal-experience")
async def signal_experience(request: Request) -> JSONResponse:
    _require_admin_key(request)
    state = _state()
    scorer = getattr(state, "experience_scorer", None)
    if scorer is None:
        raise HTTPException(status_code=503, detail="signal experience scoring is disabled")

    insights = await asyncio.to_thread(build_insights, scorer.store)
    llm_context = build_llm_context(insights)
    return JSONResponse(
        {
            "ok": True,
            "insights": insights,
            "llm_context": llm_context,
        }
    )


@router.get("/agent-logs")
async def agent_logs(request: Request, limit: int = 50) -> JSONResponse:
    _require_admin_key(request)
    state = _state()
    signals = state.signal_store.list_signals(limit=limit)
    logs = []
    for signal in signals:
        executions = []
        for name in state.slaves:
            exec_ = state.account_store.get_execution(name, signal.chat_id or "", signal.message_id or 0)
            if exec_ is not None:
                executions.append(_serialize_execution(exec_))
        logs.append(
            {
                "signal": _serialize_signal(signal),
                "executions": executions,
                "agents": [
                    {"agent": "parser", "status": "done", "detail": signal.parser_used},
                    {"agent": "risk", "status": "done", "detail": f"confidence {signal.parse_confidence:.2f}"},
                    {"agent": "router", "status": "done", "detail": f"routed to {len(state.slaves)} slaves"},
                    {"agent": "executor", "status": "done", "detail": f"{sum(1 for e in executions if e['status'] == 'executed')} executed"},
                ],
            }
        )
    return JSONResponse(logs)
