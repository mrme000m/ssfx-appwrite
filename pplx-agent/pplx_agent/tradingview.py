"""TradingView free-tier integration for multi-timeframe technical analysis.

This module is intentionally lightweight: it uses TradingView's public scanner
endpoint (``scanner.tradingview.com``) to fetch aggregated recommendation data
for XAUUSD across timeframes from 1 minute to 1 month. No websocket session or
premium subscription is required.
"""

from __future__ import annotations

from typing import Any

import requests

SCAN_URL = "https://scanner.tradingview.com/global/scan"
SEARCH_URL = "https://symbol-search.tradingview.com/symbol_search/v3"

# Indicator columns the scanner can return per timeframe.
_INDICATORS = ["Recommend.Other", "Recommend.All", "Recommend.MA"]
_TIMEFRAMES = ["1", "5", "15", "60", "240", "1D", "1W", "1M"]


def resolve_symbol_id(symbol: str = "XAUUSD", exchange: str = "OANDA") -> str:
    """Best-effort resolution of a clean symbol to a TradingView ticker id."""
    symbol = symbol.upper().strip()
    # Fast path for the most common gold mapping.
    if symbol in {"XAUUSD", "GOLD", "XAU/USD"}:
        return f"{exchange}:XAUUSD"

    params: dict[str, Any] = {"text": symbol.replace("/", ""), "start": 0}
    if ":" in symbol:
        exchange, symbol = symbol.split(":", 1)
        params["exchange"] = exchange.strip()
        params["text"] = symbol.strip()

    resp = requests.get(SEARCH_URL, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    for s in data.get("symbols", []):
        ex = s.get("exchange", "").split(" ")[0]
        prefix = s.get("prefix", "")
        sym = s.get("symbol", "")
        if prefix:
            return f"{prefix}:{sym}"
        return f"{ex.upper()}:{sym}"
    raise TradingViewError(f"Could not resolve TradingView symbol id for {symbol}")


def get_multi_timeframe_ta(symbol_id: str | None = None) -> dict[str, Any]:
    """Fetch aggregated TradingView technical analysis across all timeframes.

    Returns a dict keyed by timeframe label, e.g. ``{"M1": {"all": 0.2, ...}}``.
    Values are in the range ``[-1, 1]`` where positive = buy, negative = sell.
    """
    if symbol_id is None:
        symbol_id = resolve_symbol_id()

    columns = []
    for tf in _TIMEFRAMES:
        for ind in _INDICATORS:
            columns.append(ind if tf == "1D" else f"{ind}|{tf}")

    resp = requests.post(
        SCAN_URL,
        json={"symbols": {"tickers": [symbol_id]}, "columns": columns},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("data") or not data["data"][0]:
        return {}

    values = data["data"][0].get("d", [])
    structured: dict[str, Any] = {}
    for i, val in enumerate(values):
        col = columns[i]
        parts = col.split("|")
        name = parts[0]
        period = parts[1] if len(parts) > 1 else "D1"
        label = _normalize_tf_label(period)
        bucket = structured.setdefault(label, {})
        bucket[name.split(".")[-1].lower()] = round(val * 1000) / 500

    # Attach raw symbol_id for downstream use.
    structured["_symbol_id"] = symbol_id
    return structured


def get_timeframe_summary(symbol: str = "XAUUSD") -> dict[str, Any]:
    """Return a prompt-friendly summary of multi-timeframe technicals."""
    symbol_id = resolve_symbol_id(symbol)
    ta = get_multi_timeframe_ta(symbol_id)
    ta.pop("_symbol_id", None)

    summary: dict[str, Any] = {}
    for tf, vals in ta.items():
        all_score = vals.get("all", 0.0)
        summary[tf] = {
            "bias": _score_to_bias(all_score),
            "all": all_score,
            "ma": vals.get("ma", 0.0),
            "other": vals.get("other", 0.0),
        }
    return {
        "symbol": symbol,
        "symbol_id": symbol_id,
        "timeframes": summary,
    }


class TradingViewError(RuntimeError):
    """Errors from TradingView public endpoints."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _score_to_bias(score: float) -> str:
    if score >= 0.5:
        return "strong_buy"
    if score > 0.1:
        return "buy"
    if score <= -0.5:
        return "strong_sell"
    if score < -0.1:
        return "sell"
    return "neutral"


def _normalize_tf_label(raw: str) -> str:
    mapping = {
        "1": "M1",
        "5": "M5",
        "15": "M15",
        "60": "H1",
        "240": "H4",
        "1D": "D1",
        "1W": "W1",
        "1M": "MN1",
    }
    return mapping.get(raw, raw)
