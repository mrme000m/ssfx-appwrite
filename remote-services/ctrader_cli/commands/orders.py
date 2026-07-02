"""Order commands — create, cancel, amend."""

from __future__ import annotations

import argparse

from ctrader_client import CTraderSession
from ctrader_client.execution import OrderRequest

from ..output import Output
from ..session import parse_exec_response, resolve_symbol_id


async def cmd_order_create(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """Create a new order (MARKET, LIMIT, STOP, STOP_LIMIT)."""
    symbol_id = resolve_symbol_id(session, args.symbol)
    order_type = args.type.upper()
    requested_sl = args.sl
    requested_tp = args.tp
    market_needs_sltp_amend = order_type == "MARKET" and (
        requested_sl is not None or requested_tp is not None
    )

    req = OrderRequest(
        account_id=session.account_id,
        symbol_id=symbol_id,
        order_type=order_type,
        trade_side=args.side.upper(),
        volume_lots=args.volume,
    )

    if args.limit_price is not None:
        req.limit_price = args.limit_price
    if args.stop_price is not None:
        req.stop_price = args.stop_price
    if requested_sl is not None and not market_needs_sltp_amend:
        req.stop_loss = requested_sl
    if requested_tp is not None and not market_needs_sltp_amend:
        req.take_profit = requested_tp
    if args.label:
        req.label = args.label
    if args.comment:
        req.comment = args.comment
    if args.tif:
        req.time_in_force = args.tif

    out.info(f"Submitting {args.side} {args.type} order: {args.symbol} {args.volume} lots...")
    if market_needs_sltp_amend:
        out.info("MARKET SL/TP will be attached after fill via position amend.")

    fut = await session.execution.submit(req)
    response = await fut
    result = parse_exec_response(response, session)
    result["order_request"] = {
        "symbol": args.symbol,
        "side": args.side,
        "type": args.type,
        "volume": args.volume,
        "sl": requested_sl,
        "tp": requested_tp,
    }

    if market_needs_sltp_amend and result.get("position_id") and not result.get("error"):
        amend_response = await session.execution.amend_position_sltp(
            session.account_id,
            result["position_id"],
            stop_loss=requested_sl,
            take_profit=requested_tp,
        )
        result["market_sltp_amend"] = parse_exec_response(amend_response, session)
        result["market_sltp_amend"]["requested_sl"] = requested_sl
        result["market_sltp_amend"]["requested_tp"] = requested_tp
    elif market_needs_sltp_amend and not result.get("error"):
        result["market_sltp_amend"] = {
            "error": "POSITION_NOT_RETURNED",
            "description": "Market order response did not include a position_id; SL/TP were not attached.",
            "requested_sl": requested_sl,
            "requested_tp": requested_tp,
        }

    if out.fmt == "json":
        out.raw(result)
    else:
        out.kv(
            [(k, v) for k, v in result.items() if k != "order_request"],
            title="Order Submitted",
        )


async def cmd_order_cancel(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """Cancel a pending order."""
    out.info(f"Cancelling order {args.order_id}...")
    fut = await session.execution.cancel(session.account_id, args.order_id)
    response = await fut
    result = parse_exec_response(response, session)
    result["cancelled_order_id"] = args.order_id
    out.raw(result) if out.fmt == "json" else out.kv(
        list(result.items()), title="Order Cancelled"
    )


async def cmd_order_amend(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """Amend SL/TP on a pending order, preserving existing price fields."""
    if args.sl is None and args.tp is None and args.limit_price is None and args.stop_price is None:
        out.error("At least one of --sl, --tp, --limit-price, or --stop-price must be provided.")
        return

    # Reconcile to fetch the current order so we can preserve its price.
    res = await session.protocol.reconcile(session.account_id)
    existing = None
    for ord_ in res.order:
        if ord_.orderId == args.order_id:
            existing = ord_
            break

    if existing is None:
        out.error(f"Order {args.order_id} not found or no longer pending.")
        return

    # cTrader requires the existing limit/stop price to be resent.
    limit_price = args.limit_price
    stop_price = args.stop_price
    if limit_price is None and hasattr(existing, "limitPrice") and existing.limitPrice:
        limit_price = existing.limitPrice
    if stop_price is None and hasattr(existing, "stopPrice") and existing.stopPrice:
        stop_price = existing.stopPrice

    out.info(f"Amending order {args.order_id}...")
    response = await session.execution.amend_order(
        session.account_id,
        args.order_id,
        stop_loss=args.sl,
        take_profit=args.tp,
        limit_price=limit_price,
        stop_price=stop_price,
    )
    result = parse_exec_response(response, session)
    result["amended_order_id"] = args.order_id
    result["new_sl"] = args.sl
    result["new_tp"] = args.tp
    out.raw(result) if out.fmt == "json" else out.kv(
        list(result.items()), title="Order Amended"
    )
