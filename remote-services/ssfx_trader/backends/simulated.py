"""Simulated execution backend — logs orders without real market risk."""
from __future__ import annotations

import logging
from typing import Any

from ssfx_parser import TradeSignal

logger = logging.getLogger(__name__)


class SimulatedBackend:
    """Backend that simulates fills and tracks fake positions in memory."""

    def __init__(self, account_name: str):
        self._account_name = account_name
        self._positions: dict[int, dict[str, Any]] = {}
        self._next_position_id = 1000
        self._next_order_id = 1
        self._balance = 10000.0
        self._equity = 10000.0

    async def connect(self) -> None:
        logger.info("[%s] Simulated backend connected", self._account_name)

    async def close(self) -> None:
        logger.info("[%s] Simulated backend closed", self._account_name)

    async def get_account_summary(self) -> dict[str, float]:
        return {"balance": self._balance, "equity": self._equity}

    async def open_position(
        self, signal: TradeSignal, volume_lots: float
    ) -> dict[str, Any]:
        order_id = self._next_order_id
        self._next_order_id += 1
        position_id = self._next_position_id
        self._next_position_id += 1

        fill_price = signal.entry_price or 0.0
        self._positions[position_id] = {
            "signal": signal,
            "volume_lots": volume_lots,
            "remaining_volume_lots": volume_lots,
            "fill_price": fill_price,
        }

        logger.info(
            "[%s] SIMULATED OPEN order=%s position=%s %s %s %.2f lots @ %.5f",
            self._account_name,
            order_id,
            position_id,
            signal.direction,
            signal.symbol,
            volume_lots,
            fill_price,
        )

        return {
            "accepted": True,
            "order_id": order_id,
            "position_id": position_id,
            "executed_price": fill_price,
            "volume_lots": volume_lots,
        }

    async def close_position(
        self, position_id: int, volume_lots: float | None
    ) -> dict[str, Any]:
        pos = self._positions.get(position_id)
        if pos is None:
            return {"accepted": False, "error": "position not found"}

        close_volume = volume_lots if volume_lots is not None else pos["remaining_volume_lots"]
        pos["remaining_volume_lots"] = max(0.0, pos["remaining_volume_lots"] - close_volume)

        if pos["remaining_volume_lots"] <= 0:
            self._positions.pop(position_id, None)

        logger.info(
            "[%s] SIMULATED CLOSE position=%s volume=%.2f lots remaining=%.2f",
            self._account_name,
            position_id,
            close_volume,
            pos.get("remaining_volume_lots", 0.0),
        )

        return {"accepted": True, "closed_volume": close_volume}

    async def amend_position_sltp(
        self, position_id: int, stop_loss: float | None, take_profit: float | None
    ) -> dict[str, Any]:
        pos = self._positions.get(position_id)
        if pos is None:
            return {"accepted": False, "error": "position not found"}

        logger.info(
            "[%s] SIMULATED AMEND position=%s sl=%s tp=%s",
            self._account_name,
            position_id,
            stop_loss,
            take_profit,
        )
        return {"accepted": True}

    async def cancel_order(self, order_id: int) -> dict[str, Any]:
        logger.info("[%s] SIMULATED CANCEL order=%s", self._account_name, order_id)
        return {"accepted": True}

    async def get_open_position_ids(self) -> set[int]:
        return set(self._positions.keys())
