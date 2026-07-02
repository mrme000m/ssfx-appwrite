"""Account commands — info, positions, orders, deals, summary, cashflow, margin."""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from typing import Any

from ctrader_client import CTraderSession

from ..output import Output
from ..session import (
    parse_orders,
    parse_positions,
    resolve_symbol_id,
    resolve_symbol_name,
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


def _money_divisor(money_digits: int) -> int:
    """Compute divisor from moneyDigits field."""
    return 10 ** money_digits if money_digits else 100


async def cmd_account_info(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """Show account balance, leverage, equity, and P&L."""
    res = await session.protocol.get_trader(session.account_id)
    trader = getattr(res, "trader", res)

    money_digits = getattr(trader, "moneyDigits", 0) or 0
    divisor = _money_divisor(money_digits)

    def _money(val: Any) -> float:
        return (val or 0) / divisor

    balance = _money(getattr(trader, "balance", 0))

    pairs: list[tuple[str, Any]] = [
        ("account_id", session.account_id),
        ("login", getattr(trader, "traderLogin", "—")),
        ("balance", balance),
        ("broker", getattr(trader, "brokerName", "—")),
    ]

    lev = getattr(trader, "leverageInCents", 0)
    if lev:
        pairs.append(("leverage", f"{lev / 100:.0f}:1"))

    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
        ProtoOAAccessRights,
        ProtoOAAccountType,
    )

    access = getattr(trader, "accessRights", None)
    if access is not None:
        try:
            pairs.append(("access", ProtoOAAccessRights.Name(access)))
        except Exception:
            pass

    acct_type = getattr(trader, "accountType", None)
    if acct_type is not None:
        try:
            pairs.append(("account_type", ProtoOAAccountType.Name(acct_type)))
        except Exception:
            pass

    reg_ts = getattr(trader, "registrationTimestamp", 0)
    if reg_ts:
        # API returns ms, fromtimestamp expects seconds
        ts = reg_ts / 1000 if reg_ts > 1e10 else reg_ts
        pairs.append(
            ("registered", datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"))
        )

    pairs.append(("money_digits", money_digits))

    bonus = _money(getattr(trader, "managerBonus", 0))
    if bonus:
        pairs.append(("manager_bonus", bonus))
    ib_bonus = _money(getattr(trader, "ibBonus", 0))
    if ib_bonus:
        pairs.append(("ib_bonus", ib_bonus))

    try:
        unrealized_pnl = await session.execution.get_unrealized_pnl(session.account_id)
        equity = balance + unrealized_pnl
        pairs.append(("unrealized_pnl", unrealized_pnl))
        pairs.append(("equity", equity))
    except Exception:
        pass

    out.kv(pairs, title="Account Info")


async def cmd_account_positions(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """List open positions."""
    res = await session.protocol.reconcile(session.account_id)
    positions = parse_positions(res, session)

    if out.fmt == "json":
        out.raw({"positions": positions})
        return

    if not positions:
        out.info("No open positions.\n")
        return

    out.table(
        positions,
        columns=["position_id", "symbol", "side", "volume", "price", "sl", "tp"],
        title=f"Open Positions ({len(positions)})",
    )


async def cmd_account_orders(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """List pending orders."""
    res = await session.protocol.reconcile(session.account_id)
    orders = parse_orders(res, session)

    if out.fmt == "json":
        out.raw({"orders": orders})
        return

    if not orders:
        out.info("No pending orders.\n")
        return

    out.table(
        orders,
        columns=[
            "order_id",
            "symbol",
            "type",
            "side",
            "volume",
            "price",
            "limit_price",
            "stop_price",
            "sl",
            "tp",
        ],
        title=f"Pending Orders ({len(orders)})",
    )


async def cmd_account_deals(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """List closed deal history."""
    from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOADealListReq
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
        ProtoOADealStatus,
        ProtoOATradeSide,
    )

    now_ms = int(time.time() * 1000)
    from_ms: int = 0
    to_ms: int = now_ms

    if args.days:
        from_ms = now_ms - args.days * 86_400_000
    elif args.from_dt:
        from_ms = _parse_dt(args.from_dt)
    if args.to_dt:
        to_ms = _parse_dt(args.to_dt)

    req = ProtoOADealListReq()
    req.ctidTraderAccountId = session.account_id
    req.fromTimestamp = from_ms
    req.toTimestamp = to_ms
    if args.limit:
        req.maxRows = args.limit

    fut = await session.protocol.send(req)
    res = await fut

    deals: list[dict[str, Any]] = []
    for d in res.deal:
        sid = d.symbolId
        sym = resolve_symbol_name(session, sid)
        deal_money_digits = getattr(d, "moneyDigits", 0) or 0
        m_div = _money_divisor(deal_money_digits)

        try:
            side = ProtoOATradeSide.Name(d.tradeSide)
        except Exception:
            side = str(d.tradeSide)
        try:
            status = ProtoOADealStatus.Name(d.dealStatus)
        except Exception:
            status = str(d.dealStatus)

        row: dict[str, Any] = {
            "deal_id": d.dealId,
            "position_id": getattr(d, "positionId", 0),
            "symbol": sym,
            "side": side,
            "status": status,
            "volume": d.volume / 100,
            "price": d.executionPrice if d.executionPrice else 0,
            "commission": getattr(d, "commission", 0) / m_div,
            "time": datetime.fromtimestamp(
                d.executionTimestamp / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M"),
        }

        cpd = getattr(d, "closePositionDetail", None)
        if cpd and hasattr(cpd, "grossProfit"):
            cpd_money_digits = getattr(cpd, "moneyDigits", 0) or 0
            cpd_div = _money_divisor(cpd_money_digits)
            row["pnl"] = cpd.grossProfit / cpd_div
            row["swap"] = getattr(cpd, "swap", 0) / cpd_div
            row["entry_price"] = cpd.entryPrice if cpd.entryPrice else 0
            row["balance_after"] = getattr(cpd, "balance", 0) / cpd_div

        deals.append(row)

    if out.fmt == "json":
        out.raw({"deals": deals, "has_more": getattr(res, "hasMore", False)})
        return

    if not deals:
        out.info("No deal history found.\n")
        return

    has_more = getattr(res, "hasMore", False)
    title = f"Deal History ({len(deals)} deals{'+' if has_more else ''})"

    out.table(
        deals,
        columns=[
            "time",
            "deal_id",
            "symbol",
            "side",
            "status",
            "volume",
            "price",
            "pnl",
            "swap",
            "commission",
        ],
        title=title,
    )

    if has_more:
        out.info("(more deals available — use --limit to fetch more)\n")


async def cmd_account_summary(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """All-in-one snapshot: balance, equity, P&L, positions, orders."""
    trader_res = await session.protocol.get_trader(session.account_id)
    trader = getattr(trader_res, "trader", trader_res)

    money_digits = getattr(trader, "moneyDigits", 0) or 0
    divisor = _money_divisor(money_digits)
    balance = (getattr(trader, "balance", 0) or 0) / divisor

    # Unrealized P&L
    try:
        unrealized_pnl = await session.execution.get_unrealized_pnl(session.account_id)
    except Exception:
        unrealized_pnl = 0.0
    equity = balance + unrealized_pnl

    # Positions + Orders (single reconcile call)
    recon_res = await session.protocol.reconcile(session.account_id)
    positions = parse_positions(recon_res, session)
    orders = parse_orders(recon_res, session)

    summary: dict[str, Any] = {
        "account_id": session.account_id,
        "balance": balance,
        "equity": equity,
        "unrealized_pnl": unrealized_pnl,
        "positions_count": len(positions),
        "pending_orders_count": len(orders),
        "environment": "live" if session._use_live else "demo",
    }

    if positions:
        total_volume = sum(p.get("volume", 0) for p in positions)
        summary["total_volume"] = total_volume
        buy_count = sum(1 for p in positions if p.get("side", "").upper() == "BUY")
        sell_count = len(positions) - buy_count
        summary["buy_positions"] = buy_count
        summary["sell_positions"] = sell_count

    if out.fmt == "json":
        summary["positions"] = positions
        summary["orders"] = orders
        out.raw(summary)
        return

    pairs = [
        ("account_id", summary["account_id"]),
        ("environment", summary["environment"]),
        ("balance", summary["balance"]),
        ("equity", summary["equity"]),
        ("unrealized_pnl", summary["unrealized_pnl"]),
        ("positions", summary["positions_count"]),
        ("pending_orders", summary["pending_orders_count"]),
    ]
    if "total_volume" in summary:
        pairs.append(("total_volume", summary["total_volume"]))
        pairs.append(("buy/sell", f"{summary['buy_positions']}/{summary['sell_positions']}"))
    out.kv(pairs, title="Account Summary")

    if positions:
        out.table(
            positions,
            columns=["position_id", "symbol", "side", "volume", "price", "sl", "tp"],
            title=f"Open Positions ({len(positions)})",
        )
    if orders:
        out.table(
            orders,
            columns=[
                "order_id",
                "symbol",
                "type",
                "side",
                "volume",
                "price",
                "limit_price",
                "stop_price",
                "sl",
                "tp",
            ],
            title=f"Pending Orders ({len(orders)})",
        )


async def cmd_account_cashflow(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """List cash flow history (deposits, withdrawals, bonuses)."""
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
        ProtoOAChangeBalanceType,
    )

    now_ms = int(time.time() * 1000)
    from_ms: int = 0
    to_ms: int = now_ms

    if args.days:
        from_ms = now_ms - args.days * 86_400_000
    elif args.from_dt:
        from_ms = _parse_dt(args.from_dt)
    if args.to_dt:
        to_ms = _parse_dt(args.to_dt)

    res = await session.protocol.get_cash_flow_history(
        session.account_id, from_ts=from_ms, to_ts=to_ms
    )

    entries: list[dict[str, Any]] = []
    for dw in getattr(res, "depositWithdraw", []):
        money_digits = getattr(dw, "moneyDigits", 0) or 0
        m_div = _money_divisor(money_digits)

        op_type_val = getattr(dw, "operationType", 0)
        try:
            op_type = ProtoOAChangeBalanceType.Name(op_type_val)
        except Exception:
            op_type = str(op_type_val)

        entries.append({
            "balance_id": getattr(dw, "balanceHistoryId", 0),
            "type": op_type,
            "delta": getattr(dw, "delta", 0) / m_div,
            "balance": getattr(dw, "balance", 0) / m_div,
            "equity": getattr(dw, "equity", 0) / m_div,
            "time": datetime.fromtimestamp(
                getattr(dw, "changeBalanceTimestamp", 0) / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M"),
        })

    if out.fmt == "json":
        out.raw({"cashflow": entries})
        return

    if not entries:
        out.info("No cash flow history found.\n")
        return

    out.table(
        entries,
        columns=["time", "type", "delta", "balance", "equity"],
        title=f"Cash Flow History ({len(entries)})",
    )


async def cmd_account_margin(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """Calculate expected margin for a potential order."""
    symbol_id = resolve_symbol_id(session, args.symbol)
    result = await session.execution.get_expected_margin(
        session.account_id, symbol_id, args.volume
    )

    trader_res = await session.protocol.get_trader(session.account_id)
    trader = getattr(trader_res, "trader", trader_res)
    money_digits = getattr(trader, "moneyDigits", 0) or 0
    divisor = _money_divisor(money_digits)
    balance = (getattr(trader, "balance", 0) or 0) / divisor

    buy_margin = result["buy_margin"]
    sell_margin = result["sell_margin"]
    free_after_buy = balance - buy_margin
    free_after_sell = balance - sell_margin

    data: dict[str, Any] = {
        "symbol": args.symbol,
        "volume": args.volume,
        "buy_margin": buy_margin,
        "sell_margin": sell_margin,
        "balance": balance,
        "free_after_buy": free_after_buy,
        "free_after_sell": free_after_sell,
    }

    if out.fmt == "json":
        out.raw(data)
        return

    pairs = [
        ("symbol", args.symbol),
        ("volume", args.volume),
        ("buy_margin", buy_margin),
        ("sell_margin", sell_margin),
        ("balance", balance),
        ("free_after_buy", free_after_buy),
        ("free_after_sell", free_after_sell),
    ]
    out.kv(pairs, title="Expected Margin")
