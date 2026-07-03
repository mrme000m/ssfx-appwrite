#!/usr/bin/env python3
"""XAUUSD Min-Vol Trade Lifecycle Manager — Market-Aware.

Ad-hoc trading CLI. Connects to the local ctrader trading service (port 9300)
using admin API key, the account-hub (port 9301) for live events, and the
dataservice API (port 9002) for real-time market prices.

Location: dev/scripts/xauusd_lifecycle.py (moved from remote-services/)
— this is a development tool, not a runtime service.

Lifecycle phases:
  1. NEW     — Open a BUY/SELL XAUUSD position at min volume (0.01 lots)
  2. TP_HIT  — Partial close (TP1=50% by default, TP2=100%)
  3. SL_TO_ENTRY — Move stop-loss to entry price
  4. CLOSE_HALF   — Close 50% of remaining volume
  5. CLOSE        — Close 100% of remaining
  6. ENTRY_UPDATE — Update entry price for an active position
  7. CANCEL       — Cancel a pending order

Usage:
  ./xauusd_lifecycle.py accounts                    List active accounts from account hub
  ./xauusd_lifecycle.py market                      Show current XAUUSD market data
  ./xauusd_lifecycle.py new <grant> <ctid> [opts]   Open new XAUUSD position (auto-price if omitted)
  ./xauusd_lifecycle.py status <grant> <ctid>       Show account + position state
  ./xauusd_lifecycle.py tp <grant> <ctid> [opts]    TP hit (TP1/TP2/TP3)
  ./xauusd_lifecycle.py sl-entry <grant> <ctid>     Move SL to entry price
  ./xauusd_lifecycle.py close-half <grant> <ctid>   Close 50% of remaining
  ./xauusd_lifecycle.py close <grant> <ctid>        Close 100%
  ./xauusd_lifecycle.py lifecycle <grant> <ctid>    Full lifecycle demo (market-aware)
  ./xauusd_lifecycle.py ws <grant> <ctid>           Stream account events live
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import aiohttp

DEFAULT_TRADE_URL = os.environ.get("CTRADER_TRADE_URL", "http://localhost:9300")
DEFAULT_HUB_URL = os.environ.get("ACCOUNT_HUB_URL", "http://localhost:9301")
DEFAULT_DATA_URL = os.environ.get("DATA_SERVICE_URL", "http://localhost:9002")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "_gBGfkvMupqrpA4z68M40Dy6SS1tghwmJNDXtltKbI4")

SL_OFFSET_POINTS = 10.0
TP1_OFFSET_POINTS = 10.0
TP2_OFFSET_POINTS = 20.0

AUTH_HEADERS = {"X-Admin-Key": ADMIN_API_KEY, "Content-Type": "application/json"}


# ═══════════════════════════════════════════════════════════════════
#  Market data helpers
# ═══════════════════════════════════════════════════════════════════


@dataclass
class MarketPrice:
    bid: float = 0.0
    ask: float = 0.0
    spread: float = 0.0
    timestamp_ms: int = 0
    mid: float = 0.0

    @property
    def fmt(self) -> str:
        return f"bid={self.bid:.2f} ask={self.ask:.2f} spread={self.spread:.2f}"


async def fetch_market_price(symbol: str = "XAUUSD") -> MarketPrice | None:
    url = f"{DEFAULT_DATA_URL}/data/{symbol}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                bid = float(data.get("bid", 0))
                ask = float(data.get("ask", 0))
                return MarketPrice(
                    bid=bid,
                    ask=ask,
                    spread=float(data.get("spread", 0)),
                    timestamp_ms=data.get("timestamp_ms", 0),
                    mid=(bid + ask) / 2,
                )
    except Exception as exc:
        _log("MARKET", f"fetch error: {exc}")
        return None


def market_prices(
    mid: float,
    direction: str,
    sl_offset: float = SL_OFFSET_POINTS,
    tp1_offset: float = TP1_OFFSET_POINTS,
    tp2_offset: float = TP2_OFFSET_POINTS,
) -> dict[str, float]:
    """Calculate entry/SL/TP based on mid price and direction."""
    if direction.upper() == "BUY":
        entry = round(mid, 2)
        sl = round(mid - sl_offset, 2)
        tp1 = round(mid + tp1_offset, 2)
        tp2 = round(mid + tp2_offset, 2)
    else:
        entry = round(mid, 2)
        sl = round(mid + sl_offset, 2)
        tp1 = round(mid - tp1_offset, 2)
        tp2 = round(mid - tp2_offset, 2)
    return {"entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2}


# ═══════════════════════════════════════════════════════════════════
#  Models
# ═══════════════════════════════════════════════════════════════════


@dataclass
class TradeResult:
    accepted: bool
    status: str = ""
    order_id: int | None = None
    position_id: int | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _req_body(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[str, str, dict[str, Any] | None]:
    return method, path, body


async def _req(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    url = f"{DEFAULT_TRADE_URL}{path}"
    async with session.request(method, url, json=body, headers=AUTH_HEADERS) as resp:
        data = await resp.json()
        return resp.status, data


def _parse_result(status: int, data: dict[str, Any]) -> TradeResult:
    ok = status == 200 and data.get("status") in ("ok", "accepted")
    details = data.get("details", {})
    exec_data = details.get("execution", {}) if isinstance(details, dict) else {}
    return TradeResult(
        accepted=ok,
        status=data.get("status", "error"),
        message=data.get("message", ""),
        details=details if isinstance(details, dict) else {},
        order_id=exec_data.get("order_id") or data.get("order_id"),
        position_id=exec_data.get("position_id") or data.get("position_id"),
    )


def _ts() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S")


def _log(tag: str, msg: str, **kw: Any) -> None:
    extra = " " + " ".join(f"{k}={v}" for k, v in kw.items()) if kw else ""
    print(f"[{_ts()}] [{tag}] {msg}{extra}")


# ═══════════════════════════════════════════════════════════════════
#  Account Hub helpers
# ═══════════════════════════════════════════════════════════════════


async def fetch_accounts() -> list[dict[str, Any]]:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{DEFAULT_HUB_URL}/accounts") as resp:
            return await resp.json()


async def fetch_account(grant_id: str, ctid: int) -> dict[str, Any] | None:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{DEFAULT_HUB_URL}/accounts/{grant_id}/{ctid}") as resp:
            if resp.status == 200:
                return await resp.json()
            return None


# ═══════════════════════════════════════════════════════════════════
#  Trading operations
# ═══════════════════════════════════════════════════════════════════


async def new_position(
    grant_id: str,
    ctid: int,
    *,
    direction: str = "BUY",
    symbol: str = "XAUUSD",
    entry: float | None = None,
    sl: float | None = None,
    tp1: float | None = None,
    tp2: float | None = None,
    order_type: str = "MARKET",
) -> TradeResult:
    if entry is None:
        mp = await fetch_market_price(symbol)
        if mp is None:
            _log("NEW", "FAILED: cannot fetch market price")
            return TradeResult(accepted=False, message="market price unavailable")
        prices = market_prices(mp.mid, direction)
        entry = prices["entry"]
        sl = sl or prices["sl"]
        tp1 = tp1 or prices["tp1"]
        tp2 = tp2 or prices["tp2"]
        _log("MARKET", mp.fmt)

    _log("NEW", f"{direction} {symbol} entry={entry} sl={sl} tp1={tp1} tp2={tp2}")
    async with aiohttp.ClientSession() as s:
        status, data = await _req(s, "POST", f"/trade/{grant_id}/account/{ctid}/signals", {
            "signal_type": "NEW", "symbol": symbol, "direction": direction,
            "entry_price": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
            "order_type": order_type, "raw_text": "lifecycle new",
        })
    result = _parse_result(status, data)
    if result.accepted:
        vol = (result.details.get("execution", {}) or {}).get("volume", "?")
        _log("NEW", "accepted", order_id=result.order_id, position_id=result.position_id, volume=vol)
    else:
        reason = result.details.get("reason", "unknown") if isinstance(result.details, dict) else result.details
        _log("NEW", "REJECTED", reason=reason)
    return result


async def tp_hit(
    grant_id: str, ctid: int, tp_number: int = 1, *, close_pct: float | None = None,
) -> TradeResult:
    _log(f"TP{tp_number}", f"hit" + (f" close_pct={close_pct}%" if close_pct else ""))
    async with aiohttp.ClientSession() as s:
        body: dict[str, Any] = {
            "signal_type": "TP_HIT", "symbol": "XAUUSD", "direction": "BUY",
            "tp_hit_number": tp_number,
        }
        if close_pct is not None:
            body["close_percentage"] = close_pct
        status, data = await _req(s, "POST", f"/trade/{grant_id}/account/{ctid}/signals/TP_HIT", body)
    result = _parse_result(status, data)
    rd = result.details.get("reason", "unknown") if isinstance(result.details, dict) else result.details
    _log(f"TP{tp_number}", "OK" if result.accepted else f"REJECTED", reason=rd)
    return result


async def sl_to_entry(grant_id: str, ctid: int) -> TradeResult:
    _log("SL_TO_ENTRY", "moving SL to entry")
    async with aiohttp.ClientSession() as s:
        status, data = await _req(s, "POST", f"/trade/{grant_id}/account/{ctid}/signals/SL_TO_ENTRY", {
            "signal_type": "SL_TO_ENTRY", "symbol": "XAUUSD", "direction": "BUY",
        })
    result = _parse_result(status, data)
    rd = result.details.get("reason", "unknown") if isinstance(result.details, dict) else result.details
    _log("SL_TO_ENTRY", "OK" if result.accepted else f"REJECTED", reason=rd)
    return result


async def close_half(grant_id: str, ctid: int) -> TradeResult:
    _log("CLOSE_HALF", "closing 50%")
    async with aiohttp.ClientSession() as s:
        status, data = await _req(s, "POST", f"/trade/{grant_id}/account/{ctid}/signals/CLOSE_HALF", {
            "signal_type": "CLOSE_HALF", "symbol": "XAUUSD", "direction": "BUY",
        })
    result = _parse_result(status, data)
    rd = result.details.get("reason", "unknown") if isinstance(result.details, dict) else result.details
    _log("CLOSE_HALF", "OK" if result.accepted else f"REJECTED", reason=rd)
    return result


async def close_position(grant_id: str, ctid: int) -> TradeResult:
    _log("CLOSE", "closing 100%")
    async with aiohttp.ClientSession() as s:
        status, data = await _req(s, "POST", f"/trade/{grant_id}/account/{ctid}/signals/CLOSE", {
            "signal_type": "CLOSE", "symbol": "XAUUSD", "direction": "BUY",
        })
    result = _parse_result(status, data)
    rd = result.details.get("reason", "unknown") if isinstance(result.details, dict) else result.details
    _log("CLOSE", "OK" if result.accepted else f"REJECTED", reason=rd)
    return result


async def cancel_order(grant_id: str, ctid: int) -> TradeResult:
    _log("CANCEL", "cancelling")
    async with aiohttp.ClientSession() as s:
        status, data = await _req(s, "POST", f"/trade/{grant_id}/account/{ctid}/signals/CANCEL", {
            "signal_type": "CANCEL", "symbol": "XAUUSD", "direction": "BUY",
        })
    result = _parse_result(status, data)
    rd = result.details.get("reason", "unknown") if isinstance(result.details, dict) else result.details
    _log("CANCEL", "OK" if result.accepted else f"REJECTED", reason=rd)
    return result


async def entry_update(grant_id: str, ctid: int, new_entry: float) -> TradeResult:
    _log("ENTRY_UPDATE", f"entry -> {new_entry}")
    async with aiohttp.ClientSession() as s:
        status, data = await _req(s, "POST", f"/trade/{grant_id}/account/{ctid}/signals/ENTRY_UPDATE", {
            "signal_type": "ENTRY_UPDATE", "symbol": "XAUUSD", "direction": "BUY",
            "entry_price": new_entry,
        })
    result = _parse_result(status, data)
    rd = result.details.get("reason", "unknown") if isinstance(result.details, dict) else result.details
    _log("ENTRY_UPDATE", "OK" if result.accepted else f"REJECTED", reason=rd)
    return result


# ═══════════════════════════════════════════════════════════════════
#  WebSocket live stream
# ═══════════════════════════════════════════════════════════════════


async def stream_events(grant_id: str | None = None, ctid: int | None = None) -> None:
    ws_url = f"{DEFAULT_HUB_URL.replace('http', 'ws')}/ws"
    params: dict[str, str] = {}
    if grant_id:
        params["grant_id"] = grant_id
    if ctid is not None:
        params["ctid"] = str(ctid)
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        ws_url = f"{ws_url}?{qs}"

    _log("WS", f"connecting to {ws_url}")
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(ws_url) as ws:
            _log("WS", "connected — streaming events (Ctrl+C to stop)")
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        evt = data.get("type", "unknown")
                        if evt == "position":
                            pid = data.get("position_id", "?")
                            side = data.get("side", "?")
                            vol = data.get("volume", "?")
                            price = data.get("open_price", "?")
                            sym = data.get("symbol", "-")
                            print(f"  POS #{pid}: {side} {sym} vol={vol} @{price}")
                        elif evt == "execution":
                            ex = data.get("execution_type", "?")
                            pid = data.get("position_id", "?")
                            print(f"  EXEC {ex}: pos=#{pid}")
                        elif evt == "balance":
                            print(f"  BAL bal={data.get('balance','?')} eq={data.get('equity','?')}")
                        elif evt == "snapshot":
                            accts = data.get("accounts", [])
                            print(f"  SNAP {len(accts)} accounts")
                        else:
                            body = json.dumps(data)
                            print(f"  [{evt}] {body[:150]}")
                    except Exception:
                        print(f"  RAW: {msg.data[:200]}")
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    _log("WS", "closed")
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _log("WS", "error")
                    break


# ═══════════════════════════════════════════════════════════════════
#  Market data display
# ═══════════════════════════════════════════════════════════════════


async def show_market() -> None:
    print(f"\n{'=' * 54}")
    print(f"  XAUUSD — Live Market Data")
    print(f"{'=' * 54}")

    mp = await fetch_market_price("XAUUSD")
    if mp is None:
        print("  Unable to fetch market data.")
        return

    print(f"  Bid    : {mp.bid:.2f}")
    print(f"  Ask    : {mp.ask:.2f}")
    print(f"  Mid    : {mp.mid:.2f}")
    print(f"  Spread : {mp.spread:.2f}")

    prices_buy = market_prices(mp.mid, "BUY")
    prices_sell = market_prices(mp.mid, "SELL")

    print(f"\n  ── Signal reference (BUY) ──")
    print(f"  Entry  : {prices_buy['entry']}")
    print(f"  SL     : {prices_buy['sl']}  (-{SL_OFFSET_POINTS:.0f} pts)")
    print(f"  TP1    : {prices_buy['tp1']}  (+{TP1_OFFSET_POINTS:.0f} pts, 50% close)")
    print(f"  TP2    : {prices_buy['tp2']}  (+{TP2_OFFSET_POINTS:.0f} pts, 100% close)")

    print(f"\n  ── Signal reference (SELL) ──")
    print(f"  Entry  : {prices_sell['entry']}")
    print(f"  SL     : {prices_sell['sl']}  (+{SL_OFFSET_POINTS:.0f} pts)")
    print(f"  TP1    : {prices_sell['tp1']}  (-{TP1_OFFSET_POINTS:.0f} pts, 50% close)")
    print(f"  TP2    : {prices_sell['tp2']}  (-{TP2_OFFSET_POINTS:.0f} pts, 100% close)")
    print()


# ═══════════════════════════════════════════════════════════════════
#  Full lifecycle demo — market aware
# ═══════════════════════════════════════════════════════════════════


async def full_lifecycle(
    grant_id: str, ctid: int, *,
    direction: str = "BUY",
    entry: float | None = None,
    sl: float | None = None,
    tp1: float | None = None,
    tp2: float | None = None,
) -> None:
    # ── Fetch live market price ──
    mp = await fetch_market_price("XAUUSD")
    if mp is None:
        print("FAILED: cannot fetch XAUUSD market price")
        return

    prices = market_prices(mp.mid, direction)
    if entry is None:
        entry = prices["entry"]
    if sl is None:
        sl = prices["sl"]
    if tp1 is None:
        tp1 = prices["tp1"]
    if tp2 is None:
        tp2 = prices["tp2"]

    print("=" * 64)
    print("  XAUUSD Min-Vol Trade Lifecycle — Market-Aware")
    print("=" * 64)
    print(f"  Live    : {mp.fmt}")
    print(f"  Account : {grant_id[:20]}... / ctid={ctid}")
    print(f"  Symbol  : XAUUSD")
    print(f"  Volume  : 0.01 lots (~$40 notional)")
    print(f"  Entry   : {entry}")
    print(f"  SL      : {sl}  (risk: {abs(entry - sl):.2f} pts)")
    print(f"  TP1     : {tp1}  (+{abs(tp1 - entry):.2f} pts, 50% close)")
    print(f"  TP2     : {tp2}  (+{abs(tp2 - entry):.2f} pts, 100% close)")
    print("=" * 64)

    wait = 2.0

    # ── Phase 1: NEW ──────────────────────────────────────────
    print("\n── Phase 1: NEW position ──")
    r = await new_position(grant_id, ctid, direction=direction, entry=entry, sl=sl, tp1=tp1, tp2=tp2)
    if not r.accepted:
        print(f"  FAILED: {r.details}")
        return
    print(f"  Order #{r.order_id} | Position #{r.position_id}")
    await asyncio.sleep(wait)

    # ── Phase 2: TP1 hit (50% partial close) ─────────────────
    print("\n── Phase 2: TP1 HIT (50% partial close) ──")
    r = await tp_hit(grant_id, ctid, tp_number=1, close_pct=50)
    rd = r.details.get("reason", "") if isinstance(r.details, dict) else r.details
    print(f"  {'OK' if r.accepted else 'FAILED'}: {rd}")
    await asyncio.sleep(wait)

    # ── Phase 3: SL to entry ─────────────────────────────────
    print("\n── Phase 3: SL TO ENTRY ──")
    r = await sl_to_entry(grant_id, ctid)
    rd = r.details.get("reason", "") if isinstance(r.details, dict) else r.details
    print(f"  {'OK' if r.accepted else 'FAILED'}: {rd}")
    await asyncio.sleep(wait)

    # ── Phase 4: TP2 hit (close remaining) ───────────────────
    print("\n── Phase 4: TP2 HIT (close remaining 100%) ──")
    r = await tp_hit(grant_id, ctid, tp_number=2, close_pct=100)
    rd = r.details.get("reason", "") if isinstance(r.details, dict) else r.details
    print(f"  {'OK' if r.accepted else 'FAILED'}: {rd}")
    await asyncio.sleep(wait)

    # ── Phase 5: NEW again (for close-half + full close) ─────
    print("\n── Phase 5: NEW position (for close demo) ──")
    r = await new_position(grant_id, ctid, direction=direction, entry=entry, sl=sl, tp1=tp1, tp2=tp2)
    if r.accepted:
        print(f"  Order #{r.order_id} | Position #{r.position_id}")
    else:
        print(f"  FAILED: {r.details}")
        return
    await asyncio.sleep(wait)

    # ── Phase 6: Close half ──────────────────────────────────
    print("\n── Phase 6: CLOSE HALF ──")
    r = await close_half(grant_id, ctid)
    rd = r.details.get("reason", "") if isinstance(r.details, dict) else r.details
    print(f"  {'OK' if r.accepted else 'FAILED'}: {rd}")
    await asyncio.sleep(wait)

    # ── Phase 7: Close all ───────────────────────────────────
    print("\n── Phase 7: CLOSE (remaining 100%) ──")
    r = await close_position(grant_id, ctid)
    rd = r.details.get("reason", "") if isinstance(r.details, dict) else r.details
    print(f"  {'OK' if r.accepted else 'FAILED'}: {rd}")

    print("\n" + "=" * 64)
    print("  Lifecycle complete.")
    print("=" * 64)


# ═══════════════════════════════════════════════════════════════════
#  Status display
# ═══════════════════════════════════════════════════════════════════


async def show_status(grant_id: str, ctid: int) -> None:
    print(f"\n{'=' * 48}")
    print(f"  Status: {grant_id[:20]}... / ctid={ctid}")
    print(f"{'=' * 48}")

    acct = await fetch_account(grant_id, ctid)
    if acct:
        print(f"  State  : {acct.get('state', '?')}")
        print(f"  Balance: {acct.get('balance', '?')}")
        print(f"  Equity : {acct.get('equity', '?')}")
        print(f"  Margin : {acct.get('margin', '?')}")
        print(f"  Live   : {acct.get('is_live', '?')}")
        positions = acct.get("positions", [])
        if positions:
            print(f"  Positions ({len(positions)}):")
            for pos in positions:
                print(f"    #{pos.get('position_id','?')}: {pos.get('side','?')} "
                      f"vol={pos.get('volume','?')} @{pos.get('open_price','?')} "
                      f"SL={pos.get('stop_loss','-')} TP={pos.get('take_profit','-')} "
                      f"pnl={pos.get('profit',0)}")
        else:
            print("  Positions: none")
    else:
        print("  NOT FOUND in account hub")


async def show_accounts() -> None:
    accounts = await fetch_accounts()
    print(f"\n{'=' * 72}")
    print(f"  Account Hub — {len(accounts)} connections")
    print(f"{'=' * 72}")
    print(f"  {'USER':<12} {'CTID':>10} {'LIVE':>5} {'STATE':<16} {'BALANCE':>10} {'EQUITY':>10}")
    print(f"  {'-'*12} {'-'*10} {'-'*5} {'-'*16} {'-'*10} {'-'*10}")
    for a in accounts:
        user = a.get("username", "?")[:12]
        ctid = a.get("ctid", 0)
        live = "yes" if a.get("is_live") else "demo"
        state = a.get("state", "?")
        bal = f"{a.get('balance', 0):.0f}"
        eq = f"{a.get('equity', 0):.0f}"
        print(f"  {user:<12} {ctid:>10} {live:>5} {state:<16} {bal:>10} {eq:>10}")
    print()


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════


def _cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="XAUUSD Min-Vol Trade Lifecycle Manager")
    sp = p.add_subparsers(dest="cmd")

    sp.add_parser("accounts", help="List active accounts")
    sp.add_parser("market", help="Show current XAUUSD market data + calculated signal levels")

    sa = sp.add_parser("status", help="Show account + position state")
    sa.add_argument("grant_id")
    sa.add_argument("ctid", type=int)

    sn = sp.add_parser("new", help="Open new XAUUSD position (auto-prices from market if omitted)")
    sn.add_argument("grant_id")
    sn.add_argument("ctid", type=int)
    sn.add_argument("--direction", default="BUY", choices=["BUY", "SELL"])
    sn.add_argument("--entry", type=float, default=None)
    sn.add_argument("--sl", type=float, default=None)
    sn.add_argument("--tp1", type=float, default=None)
    sn.add_argument("--tp2", type=float, default=None)
    sn.add_argument("--order-type", default="MARKET", choices=["MARKET", "LIMIT"])

    st = sp.add_parser("tp", help="TP hit")
    st.add_argument("grant_id")
    st.add_argument("ctid", type=int)
    st.add_argument("--tp-number", type=int, default=1, choices=[1, 2, 3])
    st.add_argument("--close-pct", type=float, default=50)

    sl = sp.add_parser("sl-entry", help="Move SL to entry")
    sl.add_argument("grant_id")
    sl.add_argument("ctid", type=int)

    sc = sp.add_parser("close-half", help="Close 50%")
    sc.add_argument("grant_id")
    sc.add_argument("ctid", type=int)

    sf = sp.add_parser("close", help="Close 100%")
    sf.add_argument("grant_id")
    sf.add_argument("ctid", type=int)

    su = sp.add_parser("entry-update", help="Update entry price")
    su.add_argument("grant_id")
    su.add_argument("ctid", type=int)
    su.add_argument("new_entry", type=float)

    sca = sp.add_parser("cancel", help="Cancel pending order")
    sca.add_argument("grant_id")
    sca.add_argument("ctid", type=int)

    slf = sp.add_parser("lifecycle", help="Full lifecycle demo (market-aware)")
    slf.add_argument("grant_id")
    slf.add_argument("ctid", type=int)
    slf.add_argument("--direction", default="BUY", choices=["BUY", "SELL"])
    slf.add_argument("--entry", type=float, default=None)
    slf.add_argument("--sl", type=float, default=None)
    slf.add_argument("--tp1", type=float, default=None)
    slf.add_argument("--tp2", type=float, default=None)

    sw = sp.add_parser("ws", help="Stream events via WebSocket")
    sw.add_argument("--grant-id", default=None)
    sw.add_argument("--ctid", type=int, default=None)

    return p.parse_args()


async def amain() -> None:
    args = _cli()

    if args.cmd == "accounts":
        await show_accounts()

    elif args.cmd == "market":
        await show_market()

    elif args.cmd == "status":
        await show_status(args.grant_id, args.ctid)

    elif args.cmd == "new":
        r = await new_position(
            args.grant_id, args.ctid,
            direction=args.direction,
            entry=args.entry, sl=args.sl, tp1=args.tp1, tp2=args.tp2,
            order_type=args.order_type,
        )
        if r.accepted:
            print(json.dumps({"order_id": r.order_id, "position_id": r.position_id, "details": r.details}, indent=2, default=str))

    elif args.cmd == "tp":
        r = await tp_hit(args.grant_id, args.ctid, tp_number=args.tp_number, close_pct=args.close_pct)
        print(json.dumps({"accepted": r.accepted, "details": r.details}, indent=2, default=str))

    elif args.cmd == "sl-entry":
        r = await sl_to_entry(args.grant_id, args.ctid)
        print(json.dumps({"accepted": r.accepted, "details": r.details}, indent=2, default=str))

    elif args.cmd == "close-half":
        r = await close_half(args.grant_id, args.ctid)
        print(json.dumps({"accepted": r.accepted, "details": r.details}, indent=2, default=str))

    elif args.cmd == "close":
        r = await close_position(args.grant_id, args.ctid)
        print(json.dumps({"accepted": r.accepted, "details": r.details}, indent=2, default=str))

    elif args.cmd == "entry-update":
        r = await entry_update(args.grant_id, args.ctid, args.new_entry)
        print(json.dumps({"accepted": r.accepted, "details": r.details}, indent=2, default=str))

    elif args.cmd == "cancel":
        r = await cancel_order(args.grant_id, args.ctid)
        print(json.dumps({"accepted": r.accepted, "details": r.details}, indent=2, default=str))

    elif args.cmd == "lifecycle":
        await full_lifecycle(
            args.grant_id, args.ctid,
            direction=args.direction,
            entry=args.entry, sl=args.sl, tp1=args.tp1, tp2=args.tp2,
        )

    elif args.cmd == "ws":
        await stream_events(args.grant_id, args.ctid)

    else:
        print("No command specified. Use -h for help.")
        sys.exit(1)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
