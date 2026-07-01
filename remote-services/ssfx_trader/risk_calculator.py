"""Pure risk/volume calculations for cTrader-compatible instruments."""
from __future__ import annotations

from typing import Any


def risk_to_lots(
    risk_amount: float,
    entry_price: float,
    stop_loss: float,
    lot_size: float,
) -> float:
    """Convert a risk amount into a lot size."""
    price_diff = abs(entry_price - stop_loss)
    if price_diff <= 0 or lot_size <= 0 or risk_amount <= 0:
        return 0.0
    loss_per_lot = price_diff * lot_size
    return risk_amount / loss_per_lot


def dollar_to_lots(
    dollar_amount: float,
    price: float,
    lot_size: float,
) -> float:
    """Convert a desired position notional into a lot size."""
    if dollar_amount <= 0 or price <= 0 or lot_size <= 0:
        return 0.0
    notional_per_lot = price * lot_size
    return dollar_amount / notional_per_lot


def clamp_volume(
    volume: float,
    min_volume: float | None,
    max_volume: float | None,
    step: float | None = None,
) -> float:
    """Clamp a volume to min/max and optional step size."""
    if min_volume is not None:
        volume = max(volume, min_volume)
    if max_volume is not None:
        volume = min(volume, max_volume)
    if step is not None and step > 0:
        steps = round(volume / step)
        volume = steps * step
    return volume


def account_value_from_trader(trader_response: Any) -> dict[str, float]:
    """Extract balance/equity from a cTrader trader response."""
    nested = getattr(trader_response, "trader", None)
    if nested is not None:
        nested_balance = getattr(nested, "balance", None)
        if isinstance(nested_balance, (int, float)) and not isinstance(nested_balance, bool):
            trader = nested
        else:
            trader = trader_response
    else:
        trader = trader_response

    raw_balance = getattr(trader, "balance", 0.0)
    raw_equity = getattr(trader, "equity", raw_balance)
    if raw_equity is None:
        raw_equity = raw_balance

    money_digits = getattr(trader, "moneyDigits", 0)
    if not isinstance(money_digits, int) or isinstance(money_digits, bool):
        money_digits = 0
    divisor = 10 ** money_digits if money_digits else 100.0

    def _normalize(value: Any) -> float:
        if isinstance(value, int) and not isinstance(value, bool):
            return value / divisor
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    return {"balance": _normalize(raw_balance), "equity": _normalize(raw_equity)}
