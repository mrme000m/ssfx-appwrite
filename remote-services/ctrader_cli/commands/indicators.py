"""Indicator commands — SMA, EMA, RSI, ATR, and combined view."""

from __future__ import annotations

import argparse
from typing import Any

from ctrader_client import CTraderSession
from ctrader_client.indicators import ATR, EMA, RSI, SMA

from ..output import Output
from ..session import fetch_ohlcv, ohlcv_to_series, parse_timeframes, resolve_symbol_id


async def _compute_indicator(
    session: CTraderSession,
    symbol: str,
    timeframe: str,
    indicator_name: str,
    period: int,
    bars: int = 200,
) -> dict[str, Any]:
    """Fetch OHLCV and compute a single indicator value."""
    symbol_id = resolve_symbol_id(session, symbol)

    needed = max(period + 10, bars)
    ohlcv = await fetch_ohlcv(session, symbol_id, timeframe, bars=needed)
    if not ohlcv:
        return {"error": "no data", "symbol": symbol, "timeframe": timeframe}

    series = ohlcv_to_series(ohlcv, capacity=max(needed, 500))

    indicators: dict[str, type] = {
        "sma": SMA,
        "ema": EMA,
        "rsi": RSI,
        "atr": ATR,
    }

    cls = indicators.get(indicator_name)
    if cls is None:
        return {"error": f"unknown indicator: {indicator_name}"}

    indicator = cls(period)
    value = indicator.compute(series)
    last_bar = series.last_bar

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "indicator": indicator_name.upper(),
        "period": period,
        "value": value,
        "bars_used": series.count,
        "last_close": last_bar["close"] if last_bar else None,
    }


async def cmd_indicator_single(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """Compute a single indicator (sma, ema, rsi, or atr)."""
    indicator_name = args.indicator
    timeframes = parse_timeframes(args.timeframe)

    results: list[dict[str, Any]] = []
    for tf in timeframes:
        r = await _compute_indicator(
            session, args.symbol, tf, indicator_name, args.period, bars=args.bars
        )
        results.append(r)

    if out.fmt == "json":
        out.raw({"results": results})
        return

    if len(results) == 1:
        r = results[0]
        if "error" in r:
            out.error(f"{r['error']} for {r.get('symbol')} {r.get('timeframe')}")
            return
        out.kv(
            [
                ("symbol", r["symbol"]),
                ("timeframe", r["timeframe"]),
                ("indicator", r["indicator"]),
                ("period", r["period"]),
                ("value", r["value"]),
                ("last_close", r["last_close"]),
                ("bars_used", r["bars_used"]),
            ],
            title=f"{r['indicator']}({r['period']}) — {r['symbol']} {r['timeframe']}",
        )
    else:
        rows = []
        for r in results:
            rows.append(
                {
                    "timeframe": r["timeframe"],
                    "indicator": r["indicator"],
                    "period": r["period"],
                    "value": r["value"],
                    "last_close": r["last_close"],
                }
            )
        out.table(
            rows,
            columns=["timeframe", "indicator", "period", "value", "last_close"],
            title=f"{indicator_name.upper()}({args.period}) — {args.symbol} (Multi-TF)",
        )


async def cmd_indicator_all(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """Compute all indicators (SMA, EMA, RSI, ATR) for one or more timeframes."""
    timeframes = parse_timeframes(args.timeframe)
    symbol_id = resolve_symbol_id(session, args.symbol)

    sma_period = args.sma_period or 20
    ema_period = args.ema_period or 20
    rsi_period = args.rsi_period or 14
    atr_period = args.atr_period or 14

    max_period = max(sma_period, ema_period, rsi_period, atr_period) + 10
    bars_needed = max(max_period, args.bars)

    all_results: list[dict[str, Any]] = []

    for tf in timeframes:
        ohlcv = await fetch_ohlcv(session, symbol_id, tf, bars=bars_needed)
        if not ohlcv:
            all_results.append(
                {"symbol": args.symbol, "timeframe": tf, "error": "no data"}
            )
            continue

        series = ohlcv_to_series(ohlcv, capacity=max(bars_needed, 500))
        last_close = series.last_bar["close"] if series.last_bar else None

        computations = {
            "SMA": SMA(sma_period),
            "EMA": EMA(ema_period),
            "RSI": RSI(rsi_period),
            "ATR": ATR(atr_period),
        }

        row: dict[str, Any] = {
            "timeframe": tf,
            "last_close": last_close,
            "bars": series.count,
        }
        for name, ind in computations.items():
            row[name] = ind.compute(series)

        all_results.append(row)

    if out.fmt == "json":
        out.raw({"symbol": args.symbol, "results": all_results})
        return

    rows = []
    for r in all_results:
        if "error" in r:
            rows.append(r)
            continue
        rows.append(
            {
                "tf": r["timeframe"],
                "close": r["last_close"],
                f"SMA({sma_period})": r["SMA"],
                f"EMA({ema_period})": r["EMA"],
                f"RSI({rsi_period})": r["RSI"],
                f"ATR({atr_period})": r["ATR"],
            }
        )

    out.table(
        rows,
        columns=[
            "tf",
            "close",
            f"SMA({sma_period})",
            f"EMA({ema_period})",
            f"RSI({rsi_period})",
            f"ATR({atr_period})",
        ],
        title=f"All Indicators — {args.symbol}",
    )
