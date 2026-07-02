"""Main CLI — argparse setup, command dispatch, async runner."""

from __future__ import annotations

import argparse
import asyncio
import sys
import warnings
from collections.abc import Sequence
from typing import Awaitable, Callable

from ctrader_client import CTraderSession

from .commands.account import (
    cmd_account_cashflow,
    cmd_account_deals,
    cmd_account_info,
    cmd_account_margin,
    cmd_account_orders,
    cmd_account_positions,
    cmd_account_summary,
)
from .commands.indicators import cmd_indicator_all, cmd_indicator_single
from .commands.market_data import cmd_ohlcv, cmd_spot, cmd_symbols_info, cmd_symbols_list
from .commands.orders import cmd_order_amend, cmd_order_cancel, cmd_order_create
from .commands.positions import cmd_position_amend, cmd_position_close
from .config import load_config
from .output import Output

warnings.filterwarnings("ignore", message=".*service_identity.*")

# ── Command handlers ─────────────────────────────────────────────────────

CommandFn = Callable[[CTraderSession, argparse.Namespace, Output], Awaitable[None]]

_GLOBAL_FLAGS_WITH_VALUES = {"--format", "--account-id", "--broker-url", "--grant-id"}
_GLOBAL_BOOL_FLAGS = {"--json", "--quiet", "-q", "--live"}


def _normalize_global_args(argv: Sequence[str]) -> list[str]:
    """Move known global options before the subcommand tree.

    argparse only recognizes options defined on the root parser before the
    subcommand. This keeps legacy argparse behavior for command-specific flags
    while allowing ergonomic forms like ``account info --live``.
    """
    globals_: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in _GLOBAL_BOOL_FLAGS:
            globals_.append(arg)
            i += 1
            continue
        if arg in _GLOBAL_FLAGS_WITH_VALUES:
            globals_.append(arg)
            if i + 1 < len(argv):
                globals_.append(argv[i + 1])
                i += 2
            else:
                i += 1
            continue
        if any(arg.startswith(f"{flag}=") for flag in _GLOBAL_FLAGS_WITH_VALUES):
            globals_.append(arg)
            i += 1
            continue
        rest.append(arg)
        i += 1
    return globals_ + rest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctrader-cli",
        description="cTrader Open API command-line interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  ctrader-cli account info
  ctrader-cli account summary
  ctrader-cli account positions
  ctrader-cli account orders
  ctrader-cli account deals --days 7
  ctrader-cli account deals --from 2026-01-01 --to 2026-01-31 --limit 100
  ctrader-cli account cashflow --days 30
  ctrader-cli account margin --symbol XAUUSD --volume 0.01
  ctrader-cli symbols list --search XAU
  ctrader-cli ohlcv XAUUSD --timeframe M5,H1,D1 --bars 50
  ctrader-cli spot XAUUSD
  ctrader-cli order create --symbol XAUUSD --side BUY --type MARKET --volume 0.01
  ctrader-cli order create --symbol XAUUSD --side SELL --type LIMIT --volume 0.01 --limit-price 2050 --sl 2060 --tp 2000
  ctrader-cli order cancel --order-id 12345
  ctrader-cli order amend --order-id 12345 --sl 2040
  ctrader-cli position close --position-id 12345
  ctrader-cli position amend --position-id 12345 --sl 2040 --tp 2100
  ctrader-cli indicator sma --symbol XAUUSD --timeframe H1 --period 20
  ctrader-cli indicator rsi --symbol XAUUSD --timeframe M5,H1,D1 --period 14
  ctrader-cli indicator all --symbol XAUUSD --timeframe H1,D1

Agentic usage (JSON output, no banners):
  ctrader-cli --json account summary
  ctrader-cli --json account deals --days 7
  ctrader-cli --json account margin --symbol XAUUSD --volume 0.01
""",
    )

    # Global options
    parser.add_argument("--live", action="store_true", help="Use live environment")
    parser.add_argument("--account-id", type=int, default=None, help="Override account ID")
    parser.add_argument(
        "--broker-url",
        default=None,
        help="Auth broker / ctrader-internal URL (default: $CTRADER_AUTH_BROKER_URL)",
    )
    parser.add_argument(
        "--grant-id",
        default=None,
        help="Grant ID from Appwrite auth layer (default: $CTRADER_AUTH_GRANT_ID)",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Shorthand for --format json (for agentic/automated usage)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress informational messages (errors still printed)",
    )

    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    # ── account ──────────────────────────────────────────────────────────
    acct = sub.add_parser("account", help="Account info, positions, orders, deals")
    acct_sub = acct.add_subparsers(dest="subcommand", required=True)
    acct_sub.add_parser("info", help="Show balance, equity, margin, P&L")
    acct_sub.add_parser("positions", help="List open positions")
    acct_sub.add_parser("orders", help="List pending orders")
    acct_sub.add_parser("summary", help="All-in-one: balance+positions+orders+P&L")

    deals_cmd = acct_sub.add_parser("deals", help="List closed deal history")
    deals_cmd.add_argument("--days", type=int, default=None, help="Days back from now")
    deals_cmd.add_argument("--from", dest="from_dt", default=None, help="Start date (YYYY-MM-DD)")
    deals_cmd.add_argument("--to", dest="to_dt", default=None, help="End date (YYYY-MM-DD)")
    deals_cmd.add_argument("--limit", type=int, default=None, help="Max deals to fetch")

    cashflow_cmd = acct_sub.add_parser("cashflow", help="List cash flow history (deposits, withdrawals)")
    cashflow_cmd.add_argument("--days", type=int, default=None, help="Days back from now")
    cashflow_cmd.add_argument("--from", dest="from_dt", default=None, help="Start date (YYYY-MM-DD)")
    cashflow_cmd.add_argument("--to", dest="to_dt", default=None, help="End date (YYYY-MM-DD)")

    margin_cmd = acct_sub.add_parser("margin", help="Calculate expected margin for a trade")
    margin_cmd.add_argument("--symbol", required=True, help="Symbol name (e.g. XAUUSD)")
    margin_cmd.add_argument("--volume", "-v", type=float, required=True, help="Volume in lots")

    # ── symbols ──────────────────────────────────────────────────────────
    sym = sub.add_parser("symbols", help="List and search symbols")
    sym_sub = sym.add_subparsers(dest="subcommand", required=True)
    sym_list = sym_sub.add_parser("list", help="List available symbols")
    sym_list.add_argument("--search", default=None, help="Filter by name pattern")
    sym_list.add_argument("--limit", type=int, default=50, help="Max symbols to show")
    sym_info = sym_sub.add_parser("info", help="Show symbol details")
    sym_info.add_argument("symbol", help="Symbol name (e.g. XAUUSD)")

    # ── ohlcv ────────────────────────────────────────────────────────────
    ohlcv = sub.add_parser("ohlcv", help="Fetch historical OHLCV data")
    ohlcv.add_argument("symbol", help="Symbol name (e.g. XAUUSD)")
    ohlcv.add_argument(
        "--timeframe",
        "-t",
        default="H1",
        help="Timeframe(s), comma-separated (e.g. M5,H1,D1). Default: H1",
    )
    ohlcv.add_argument("--bars", "-n", type=int, default=50, help="Number of bars (default: 50)")
    ohlcv.add_argument("--from", dest="from_dt", default=None, help="Start datetime (YYYY-MM-DD)")
    ohlcv.add_argument("--to", dest="to_dt", default=None, help="End datetime (YYYY-MM-DD)")

    # ── spot ─────────────────────────────────────────────────────────────
    spot = sub.add_parser("spot", help="Get current spot price")
    spot.add_argument("symbol", help="Symbol name (e.g. XAUUSD)")

    # ── order ────────────────────────────────────────────────────────────
    order = sub.add_parser("order", help="Create, cancel, amend orders")
    order_sub = order.add_subparsers(dest="subcommand", required=True)

    order_create = order_sub.add_parser("create", help="Create a new order")
    order_create.add_argument("--symbol", required=True, help="Symbol name")
    order_create.add_argument(
        "--side", required=True, choices=["BUY", "SELL", "buy", "sell"], help="Trade side"
    )
    order_create.add_argument(
        "--type",
        required=True,
        choices=["MARKET", "LIMIT", "STOP", "STOP_LIMIT"],
        help="Order type",
    )
    order_create.add_argument("--volume", "-v", type=float, required=True, help="Volume in lots")
    order_create.add_argument("--limit-price", type=float, default=None, help="Limit price (LIMIT/STOP_LIMIT)")
    order_create.add_argument("--stop-price", type=float, default=None, help="Stop price (STOP/STOP_LIMIT)")
    order_create.add_argument(
        "--sl",
        type=float,
        default=None,
        help="Stop loss price. For MARKET orders, the CLI opens first then amends the filled position.",
    )
    order_create.add_argument(
        "--tp",
        type=float,
        default=None,
        help="Take profit price. For MARKET orders, the CLI opens first then amends the filled position.",
    )
    order_create.add_argument("--tif", default=None, help="Time in force (GTC/DAY/IOC/FOK)")
    order_create.add_argument("--label", default=None, help="Order label (max 100 chars)")
    order_create.add_argument("--comment", default=None, help="Order comment (max 512 chars)")

    order_cancel = order_sub.add_parser("cancel", help="Cancel a pending order")
    order_cancel.add_argument("--order-id", type=int, required=True, help="Order ID to cancel")

    order_amend = order_sub.add_parser("amend", help="Amend SL/TP/price on a pending order")
    order_amend.add_argument("--order-id", type=int, required=True, help="Order ID to amend")
    order_amend.add_argument("--sl", type=float, default=None, help="New stop loss price")
    order_amend.add_argument("--tp", type=float, default=None, help="New take profit price")
    order_amend.add_argument("--limit-price", type=float, default=None, help="New limit price")
    order_amend.add_argument("--stop-price", type=float, default=None, help="New stop price")

    # ── position ─────────────────────────────────────────────────────────
    pos = sub.add_parser("position", help="Close or amend positions")
    pos_sub = pos.add_subparsers(dest="subcommand", required=True)

    pos_close = pos_sub.add_parser("close", help="Close a position")
    pos_close.add_argument("--position-id", type=int, required=True, help="Position ID to close")
    pos_close.add_argument("--volume", type=float, default=None, help="Volume to close (default: full)")
    pos_close.add_argument("--symbol-id", type=int, default=None, help="Symbol ID (auto-resolved if omitted)")

    pos_amend = pos_sub.add_parser("amend", help="Amend SL/TP on a position")
    pos_amend.add_argument("--position-id", type=int, required=True, help="Position ID to amend")
    pos_amend.add_argument("--sl", type=float, default=None, help="New stop loss price")
    pos_amend.add_argument("--tp", type=float, default=None, help="New take profit price")
    pos_amend.add_argument("--trailing", action="store_true", default=None, help="Enable trailing stop loss")

    # ── indicator ────────────────────────────────────────────────────────
    ind = sub.add_parser("indicator", help="Compute technical indicators")
    ind_sub = ind.add_subparsers(dest="subcommand", required=True)

    for name, help_text in [("sma", "Simple Moving Average"), ("ema", "Exponential Moving Average"),
                             ("rsi", "Relative Strength Index"), ("atr", "Average True Range")]:
        ind_cmd = ind_sub.add_parser(name, help=help_text)
        ind_cmd.add_argument("--symbol", required=True, help="Symbol name")
        ind_cmd.add_argument(
            "--timeframe", "-t", default="H1", help="Timeframe(s), comma-separated (default: H1)"
        )
        ind_cmd.add_argument("--period", "-p", type=int, default=14 if name in ("rsi", "atr") else 20, help="Period")
        ind_cmd.add_argument("--bars", type=int, default=200, help="Historical bars to fetch")

    ind_all = ind_sub.add_parser("all", help="Compute all indicators at once")
    ind_all.add_argument("--symbol", required=True, help="Symbol name")
    ind_all.add_argument(
        "--timeframe", "-t", default="H1", help="Timeframe(s), comma-separated (default: H1)"
    )
    ind_all.add_argument("--bars", type=int, default=200, help="Historical bars to fetch")
    ind_all.add_argument("--sma-period", type=int, default=None, help="SMA period (default: 20)")
    ind_all.add_argument("--ema-period", type=int, default=None, help="EMA period (default: 20)")
    ind_all.add_argument("--rsi-period", type=int, default=None, help="RSI period (default: 14)")
    ind_all.add_argument("--atr-period", type=int, default=None, help="ATR period (default: 14)")

    return parser


# ── Command dispatch ─────────────────────────────────────────────────────

_DISPATCH: dict[tuple[str, str | None], CommandFn] = {}


def _register(command: str, subcommand: str | None = None) -> Callable[[CommandFn], CommandFn]:
    def decorator(fn: CommandFn) -> CommandFn:
        _DISPATCH[(command, subcommand)] = fn
        return fn

    return decorator


_register("account", "info")(cmd_account_info)
_register("account", "positions")(cmd_account_positions)
_register("account", "orders")(cmd_account_orders)
_register("account", "deals")(cmd_account_deals)
_register("account", "summary")(cmd_account_summary)
_register("account", "cashflow")(cmd_account_cashflow)
_register("account", "margin")(cmd_account_margin)

_register("symbols", "list")(cmd_symbols_list)
_register("symbols", "info")(cmd_symbols_info)

_register("ohlcv", None)(cmd_ohlcv)
_register("spot", None)(cmd_spot)

_register("order", "create")(cmd_order_create)
_register("order", "cancel")(cmd_order_cancel)
_register("order", "amend")(cmd_order_amend)

_register("position", "close")(cmd_position_close)
_register("position", "amend")(cmd_position_amend)

_register("indicator", "sma")(cmd_indicator_single)
_register("indicator", "ema")(cmd_indicator_single)
_register("indicator", "rsi")(cmd_indicator_single)
_register("indicator", "atr")(cmd_indicator_single)
_register("indicator", "all")(cmd_indicator_all)


async def _run(args: argparse.Namespace) -> int:
    cfg = load_config(
        use_live=args.live if args.live else None,
        account_id=args.account_id,
        broker_url=args.broker_url,
        grant_id=args.grant_id,
    )

    if not cfg.is_valid:
        print(
            "ERROR: cTrader configuration incomplete.\n"
            "Credentials are read from environment variables or .env.\n\n"
            "Required environment variables:\n"
            "  CTRADER_CLIENT_ID        cTrader OAuth client ID\n"
            "  CTRADER_CLIENT_SECRET    cTrader OAuth client secret\n"
            "  CTRADER_ACCOUNT_ID       cTrader account ID\n"
            "  CTRADER_AUTH_BROKER_URL  ctrader-internal / auth broker URL\n"
            "  CTRADER_AUTH_GRANT_ID    Grant ID from Appwrite auth layer\n"
            "  INTERNAL_API_KEY         Internal API key for ctrader-internal\n\n"
            "CLI overrides:\n"
            "  --live         Use live environment\n"
            "  --account-id   Override account ID\n"
            "  --broker-url   Override auth broker URL\n"
            "  --grant-id     Override grant ID",
            file=sys.stderr,
        )
        return 1

    fmt = "json" if args.json else args.format
    quiet = args.quiet
    out = Output(fmt=fmt, quiet=quiet)

    from .session import create_session

    session = create_session(cfg)

    try:
        out.info(f"Connecting to cTrader ({'live' if cfg.use_live else 'demo'})...")
        await session.start()
        out.info(f"Session started — account {session.account_id}\n")

        command = args.command
        subcommand = getattr(args, "subcommand", None)

        if command == "indicator" and subcommand in ("sma", "ema", "rsi", "atr"):
            args.indicator = subcommand
            fn = _DISPATCH.get(("indicator", subcommand))
        else:
            fn = _DISPATCH.get((command, subcommand))
            if fn is None:
                fn = _DISPATCH.get((command, None))

        if fn is None:
            out.error(f"Unknown command: {command} {subcommand}")
            return 1

        await fn(session, args, out)
        return 0

    except ValueError as e:
        out.error(str(e))
        return 1
    except Exception as e:
        out.error(f"{type(e).__name__}: {e}")
        return 1
    finally:
        try:
            await session.stop()
        except Exception:
            pass


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args(_normalize_global_args(sys.argv[1:]))
    exit_code = asyncio.run(_run(args))
    sys.exit(exit_code)
