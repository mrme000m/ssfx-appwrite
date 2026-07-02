"""Market data commands — symbols, ohlcv (multi-TF), spot."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from typing import Any

from ctrader_client import CTraderSession

from ..output import Output
from ..session import (
    fetch_ohlcv,
    parse_timeframes,
    resolve_symbol_id,
)


def _parse_dt(s: str) -> int:
    """Parse a date/datetime string to Unix ms timestamp."""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime: {s}. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS")


async def cmd_symbols_list(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """List or search available symbols."""
    symbols = []
    search = args.search.upper() if args.search else None
    for sid, info in session.market_data._symbols.items():
        if search and search not in info.name.upper():
            continue
        symbols.append(
            {
                "symbol_id": sid,
                "name": info.name,
                "digits": info.digits,
                "lot_size": info.lot_size,
            }
        )

    symbols.sort(key=lambda s: s["name"])

    if args.limit and len(symbols) > args.limit:
        total = len(symbols)
        symbols = symbols[: args.limit]
        if out.fmt != "json":
            out.info(f"Showing {len(symbols)} of {total} symbols (use --limit to change)\n")

    if out.fmt == "json":
        out.raw({"symbols": symbols, "total": len(symbols)})
    else:
        out.table(symbols, columns=["symbol_id", "name", "digits", "lot_size"], title="Symbols")


async def cmd_symbols_info(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """Show detailed info for a specific symbol."""
    symbol_id = resolve_symbol_id(session, args.symbol)
    info = session.market_data._symbols.get(symbol_id)

    if info is None:
        out.error(f"Symbol '{args.symbol}' not found.")
        return

    pairs = [
        ("name", info.name),
        ("symbol_id", symbol_id),
        ("digits", info.digits),
        ("pip_size", info.pip_size),
        ("lot_size", info.lot_size),
        ("min_volume", info.min_volume),
        ("step_volume", info.step_volume),
    ]
    out.kv(pairs, title=f"Symbol Info: {info.name}")


async def cmd_ohlcv(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """Fetch historical OHLCV data for one or more timeframes."""
    timeframes = parse_timeframes(args.timeframe)
    symbol_id = resolve_symbol_id(session, args.symbol)

    from_ms: int | None = None
    to_ms: int | None = None
    if args.from_dt:
        from_ms = _parse_dt(args.from_dt)
    if args.to_dt:
        to_ms = _parse_dt(args.to_dt)

    all_data: dict[str, Any] = {}

    for tf in timeframes:
        bars = await fetch_ohlcv(
            session,
            symbol_id,
            tf,
            bars=args.bars,
            from_ms=from_ms,
            to_ms=to_ms,
        )
        all_data[tf] = bars

    if out.fmt == "json":
        out.raw({"symbol": args.symbol, "data": all_data})
        return

    for tf in timeframes:
        bars = all_data[tf]
        if not bars:
            out.info(f"No data for {args.symbol} {tf}\n")
            continue

        rows = []
        for bar in bars[-args.bars :]:
            rows.append(
                {
                    "time": datetime.fromtimestamp(
                        bar["timestamp"] / 1000, tz=timezone.utc
                    ).strftime("%Y-%m-%d %H:%M"),
                    "open": bar["open"],
                    "high": bar["high"],
                    "low": bar["low"],
                    "close": bar["close"],
                    "volume": bar["volume"],
                }
            )
        out.table(
            rows,
            columns=["time", "open", "high", "low", "close", "volume"],
            title=f"{args.symbol} — {tf} ({len(rows)} bars)",
        )


async def cmd_spot(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """Get the current spot price for a symbol."""
    symbol_id = resolve_symbol_id(session, args.symbol)

    tick = session.market_data.get_last_tick(symbol_id)
    if tick is None:
        await session.market_data.subscribe_spots(session.account_id, [symbol_id])
        await asyncio.sleep(3)
        tick = session.market_data.get_last_tick(symbol_id)

    if tick is None:
        out.error(f"No spot data received for {args.symbol}. Market may be closed.")
        return

    spread = tick.ask - tick.bid
    pairs = [
        ("symbol", args.symbol),
        ("bid", tick.bid),
        ("ask", tick.ask),
        ("spread", spread),
        ("timestamp", tick.timestamp_ms),
    ]
    out.kv(pairs, title=f"Spot: {args.symbol}")
