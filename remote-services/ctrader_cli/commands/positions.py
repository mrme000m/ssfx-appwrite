"""Position commands — close, amend."""

from __future__ import annotations

import argparse

from ctrader_client import CTraderSession

from ..output import Output
from ..session import parse_exec_response


async def cmd_position_close(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """Close a position (fully or partially)."""
    symbol_id: int | None = args.symbol_id

    if symbol_id is None:
        res = await session.protocol.reconcile(session.account_id)
        for pos in res.position:
            if pos.positionId == args.position_id:
                symbol_id = pos.tradeData.symbolId
                break

    if symbol_id is None:
        out.error(f"Position {args.position_id} not found. Use --symbol-id to specify.")
        return

    vol_str = f" ({args.volume} lots)" if args.volume else " (full)"
    out.info(f"Closing position {args.position_id}{vol_str}...")
    fut = await session.execution.close_position(
        session.account_id,
        args.position_id,
        symbol_id,
        volume_lots=args.volume,
    )
    response = await fut
    result = parse_exec_response(response, session)
    result["closed_position_id"] = args.position_id
    result["volume"] = args.volume if args.volume else "full"
    out.raw(result) if out.fmt == "json" else out.kv(
        list(result.items()), title="Position Close"
    )


async def cmd_position_amend(
    session: CTraderSession, args: argparse.Namespace, out: Output
) -> None:
    """Amend SL/TP on an open position."""
    if args.sl is None and args.tp is None and args.trailing is None:
        out.error("At least one of --sl, --tp, or --trailing must be provided.")
        return

    out.info(f"Amending position {args.position_id}...")
    response = await session.execution.amend_position_sltp(
        session.account_id,
        args.position_id,
        stop_loss=args.sl,
        take_profit=args.tp,
        trailing_stop_loss=args.trailing,
    )
    result = parse_exec_response(response, session)
    result["amended_position_id"] = args.position_id
    result["new_sl"] = args.sl
    result["new_tp"] = args.tp
    result["trailing"] = args.trailing
    out.raw(result) if out.fmt == "json" else out.kv(
        list(result.items()), title="Position Amended"
    )
