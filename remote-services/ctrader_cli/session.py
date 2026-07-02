"""Session helpers — creation, symbol resolution, data parsing."""

from __future__ import annotations

from typing import Any

from ctrader_client import CTraderSession
from ctrader_client.market_data import MarketDataManager
from ctrader_client.series import OHLCVSeries

from .config import CliConfig

# ── Timeframe helpers ─────────────────────────────────────────────────────

TIMEFRAME_MINUTES: dict[str, int] = {
    "M1": 1,
    "M2": 2,
    "M3": 3,
    "M5": 5,
    "M10": 10,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "H12": 720,
    "D1": 1440,
    "W1": 10080,
    "MN1": 43200,
}

VALID_TIMEFRAMES = list(TIMEFRAME_MINUTES.keys())


def parse_timeframes(tf_str: str) -> list[str]:
    """Parse a comma-separated timeframe string (e.g. 'M5,H1,D1')."""
    parts = [t.strip().upper() for t in tf_str.split(",") if t.strip()]
    invalid = [t for t in parts if t not in TIMEFRAME_MINUTES]
    if invalid:
        raise ValueError(
            f"Invalid timeframe(s): {invalid}. Valid: {', '.join(VALID_TIMEFRAMES)}"
        )
    return parts


# ── Session creation ──────────────────────────────────────────────────────


def create_session(cfg: CliConfig) -> CTraderSession:
    """Create a CTraderSession from CLI config.

    When Appwrite auth mode is active (broker_url + grant_id + internal_api_key)
    the session is wired to use the ctrader-internal Appwrite Function for
    token refresh via BrokerTokenManager (which supports x-internal-key).
    """
    kwargs: dict[str, Any] = {
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "access_token": "",
        "refresh_token": "",
        "account_id": cfg.account_id,
        "use_live": cfg.use_live,
    }
    if cfg.is_appwrite_mode:
        kwargs["broker_url"] = cfg.broker_url
        kwargs["grant_id"] = cfg.grant_id
        kwargs["internal_api_key"] = cfg.internal_api_key
    return CTraderSession(**kwargs)


# ── Symbol resolution ────────────────────────────────────────────────────


def resolve_symbol_id(session: CTraderSession, name: str) -> int:
    """Resolve a symbol name (e.g. 'XAUUSD') to its numeric ID."""
    upper = name.upper()
    for sid, info in session.market_data._symbols.items():
        if info.name.upper() == upper:
            return sid
    raise ValueError(
        f"Symbol '{name}' not found. Use 'symbols list' to see available symbols."
    )


def resolve_symbol_name(session: CTraderSession, symbol_id: int) -> str:
    """Resolve a numeric symbol ID to its name."""
    info = session.market_data._symbols.get(symbol_id)
    return info.name if info else str(symbol_id)


def get_digits(session: CTraderSession, symbol_id: int) -> int:
    info = session.market_data._symbols.get(symbol_id)
    return info.digits if info else 5


def get_lot_size(session: CTraderSession, symbol_id: int) -> int:
    """Return the lot size in real base-currency units for a symbol."""
    info = session.market_data._symbols.get(symbol_id)
    return info.lot_size if info else 100_000


def api_volume_to_lots(session: CTraderSession, symbol_id: int, raw_volume: int) -> float:
    """Convert cTrader API volume (1/100th of a unit) to lots."""
    lot_size = get_lot_size(session, symbol_id)
    return raw_volume / (lot_size * 100)


# ── Historical OHLCV ─────────────────────────────────────────────────────


async def fetch_ohlcv(
    session: CTraderSession,
    symbol_id: int,
    timeframe: str,
    *,
    bars: int = 100,
    from_ms: int | None = None,
    to_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch historical OHLCV bars and return as list of dicts.

    Bars are returned in chronological order (oldest first).
    """
    import time

    if from_ms is not None or to_ms is not None:
        response = await session.market_data.get_historical_trendbars(
            session.account_id,
            symbol_id,
            timeframe,
            from_timestamp=from_ms or 0,
            to_timestamp=to_ms or int(time.time() * 1000),
        )
    else:
        response = await session.market_data.get_historical_trendbars(
            session.account_id,
            symbol_id,
            timeframe,
            to_timestamp=int(time.time() * 1000),
            count=bars,
        )

    digits = get_digits(session, symbol_id)
    result: list[dict[str, Any]] = []
    for tb in response.trendbar:
        low_raw = tb.low
        result.append(
            {
                "timestamp": int(getattr(tb, "utcTimestampInMinutes", 0)) * 60_000,
                "open": MarketDataManager.price_from_relative(low_raw + tb.deltaOpen, digits),
                "high": MarketDataManager.price_from_relative(low_raw + tb.deltaHigh, digits),
                "low": MarketDataManager.price_from_relative(low_raw, digits),
                "close": MarketDataManager.price_from_relative(low_raw + tb.deltaClose, digits),
                "volume": MarketDataManager.volume_from_api(tb.volume),
            }
        )
    return result


def ohlcv_to_series(bars: list[dict[str, Any]], capacity: int | None = None) -> OHLCVSeries:
    """Convert a list of OHLCV dicts into an OHLCVSeries for indicator computation."""
    if capacity is None:
        capacity = max(len(bars), 500)
    series = OHLCVSeries(capacity=capacity)
    for bar in bars:
        series.push(
            bar["open"],
            bar["high"],
            bar["low"],
            bar["close"],
            bar.get("volume", 0.0),
            bar.get("timestamp", 0),
        )
    return series


# ── Reconcile parsing ────────────────────────────────────────────────────


def _safe_enum_name(enum_cls: Any, value: int) -> str:
    try:
        return enum_cls.Name(value)
    except Exception:
        return str(value)


def parse_positions(response: Any, session: CTraderSession) -> list[dict[str, Any]]:
    """Parse positions from a ProtoOAReconcileRes."""
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATradeSide

    positions: list[dict[str, Any]] = []
    for pos in response.position:
        td = pos.tradeData
        sid = td.symbolId
        digits = get_digits(session, sid)
        sym_name = resolve_symbol_name(session, sid)
        sl = getattr(pos, "stopLoss", 0.0)
        tp = getattr(pos, "takeProfit", 0.0)
        price = getattr(pos, "price", 0.0)
        positions.append(
            {
                "position_id": pos.positionId,
                "symbol": sym_name,
                "symbol_id": sid,
                "side": _safe_enum_name(ProtoOATradeSide, td.tradeSide),
                "volume": api_volume_to_lots(session, sid, td.volume),
                "price": round(price, digits) if price else 0.0,
                "sl": round(sl, digits) if sl else None,
                "tp": round(tp, digits) if tp else None,
                "opened": int(getattr(pos, "openTimestamp", 0)),
            }
        )
    return positions


def parse_orders(response: Any, session: CTraderSession) -> list[dict[str, Any]]:
    """Parse pending orders from a ProtoOAReconcileRes."""
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
        ProtoOAOrderType,
        ProtoOATradeSide,
    )

    orders: list[dict[str, Any]] = []
    for ord_ in response.order:
        td = ord_.tradeData
        sid = td.symbolId
        digits = get_digits(session, sid)
        sym_name = resolve_symbol_name(session, sid)
        sl = getattr(ord_, "stopLoss", 0.0)
        tp = getattr(ord_, "takeProfit", 0.0)
        price_raw = getattr(ord_, "price", 0.0)
        limit_raw = getattr(ord_, "limitPrice", 0.0)
        stop_raw = getattr(ord_, "stopPrice", 0.0)
        orders.append(
            {
                "order_id": ord_.orderId,
                "symbol": sym_name,
                "symbol_id": sid,
                "type": _safe_enum_name(ProtoOAOrderType, ord_.orderType),
                "side": _safe_enum_name(ProtoOATradeSide, td.tradeSide),
                "volume": api_volume_to_lots(session, sid, td.volume),
                "price": round(price_raw, digits) if price_raw else None,
                "limit_price": round(limit_raw, digits) if limit_raw else None,
                "stop_price": round(stop_raw, digits) if stop_raw else None,
                "sl": round(sl, digits) if sl else None,
                "tp": round(tp, digits) if tp else None,
            }
        )
    return orders


# ── Execution response helpers ──────────────────────────────────────────


def parse_exec_response(response: Any, session: CTraderSession | None = None) -> dict[str, Any]:
    """Best-effort extraction of useful fields from an execution response."""
    result: dict[str, Any] = {}

    exec_type = getattr(response, "executionType", None)
    if exec_type is not None:
        try:
            from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
                ProtoOAExecutionType,
            )

            result["execution_type"] = ProtoOAExecutionType.Name(exec_type)
        except Exception:
            result["execution_type"] = str(exec_type)

    order = getattr(response, "order", None)
    if order:
        try:
            from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
                ProtoOAOrderStatus,
                ProtoOAOrderType,
                ProtoOATradeSide,
            )
        except Exception:
            ProtoOAOrderStatus = ProtoOAOrderType = ProtoOATradeSide = None

        trade_data = getattr(order, "tradeData", None)
        symbol_id = getattr(trade_data, "symbolId", None) or getattr(order, "symbolId", None)
        raw_vol = getattr(trade_data, "volume", 0) or getattr(order, "volume", 0)

        result["order_id"] = getattr(order, "orderId", None)
        if ProtoOAOrderStatus is not None:
            result["order_status"] = _safe_enum_name(
                ProtoOAOrderStatus, getattr(order, "orderStatus", 0)
            )
        else:
            result["order_status"] = str(getattr(order, "orderStatus", ""))

        order_type = getattr(order, "orderType", None)
        if order_type is not None:
            result["order_type"] = (
                _safe_enum_name(ProtoOAOrderType, order_type)
                if ProtoOAOrderType is not None
                else str(order_type)
            )

        side = getattr(trade_data, "tradeSide", None) if trade_data is not None else None
        if side is not None:
            result["side"] = (
                _safe_enum_name(ProtoOATradeSide, side)
                if ProtoOATradeSide is not None
                else str(side)
            )

        if session is not None and symbol_id:
            result["symbol"] = resolve_symbol_name(session, symbol_id)
            result["symbol_id"] = symbol_id
            result["volume"] = api_volume_to_lots(session, symbol_id, raw_vol)
        else:
            result["volume"] = raw_vol / 100 if raw_vol else 0.0

    position = getattr(response, "position", None)
    if position:
        result["position_id"] = getattr(position, "positionId", None)

    error = getattr(response, "errorCode", "")
    if error:
        result["error"] = str(error)
        result["description"] = str(getattr(response, "description", ""))

    return result
