"""Risk Manager — pre-trade checks, exposure tracking, circuit breaker."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .execution import OrderRequest

logger = logging.getLogger(__name__)


class RiskViolation(Exception):
    """Raised when an order fails a risk check."""


@dataclass
class RiskConfig:
    """Risk management configuration."""

    max_position_lots: float = 10.0
    max_open_positions: int = 20
    max_daily_drawdown_pct: float = 0.05  # 5% of account equity
    max_symbol_exposure_lots: float = 5.0
    circuit_breaker_losses: int = 5  # consecutive losses trigger halt
    max_order_volume_lots: float = 10.0


class RiskManager:
    """Pre-trade risk checks that intercept order requests.

    Usage:
        risk = RiskManager(RiskConfig(...))
        risk.check(order_request)  # raises RiskViolation on violation
        execution.submit(order_request)
    """

    def __init__(self, config: RiskConfig):
        self._cfg = config
        self._open_lots: dict[int, float] = {}  # symbol_id → net lots
        self._position_count: int = 0
        self._consecutive_losses: int = 0
        self._halted: bool = False
        self._daily_pnl: float = 0.0
        self._initial_equity: float | None = None

    def set_initial_equity(self, equity: float) -> None:
        """Set the starting equity for drawdown calculation."""
        self._initial_equity = equity

    def check(self, req: OrderRequest) -> None:
        """Run all risk checks. Raises RiskViolation on failure."""
        if self._halted:
            raise RiskViolation("Circuit breaker active — trading halted")

        self._check_position_size(req)
        self._check_symbol_exposure(req)
        self._check_max_positions()
        self._check_max_order_volume(req)

    def record_fill(self, symbol_id: int, lots: float, side: str) -> None:
        """Update exposure tracking after a fill."""
        delta = lots if side == "BUY" else -lots
        self._open_lots[symbol_id] = self._open_lots.get(symbol_id, 0.0) + delta
        self._position_count += 1

    def record_close(
        self, symbol_id: int, lots: float, pnl: float = 0.0
    ) -> None:
        """Update tracking after a position close."""
        self._open_lots[symbol_id] = max(
            0.0, self._open_lots.get(symbol_id, 0.0) - lots
        )
        self._position_count = max(0, self._position_count - 1)
        self._daily_pnl += pnl

        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        if self._consecutive_losses >= self._cfg.circuit_breaker_losses:
            self._halted = True
            logger.warning(
                "Circuit breaker triggered: %d consecutive losses",
                self._consecutive_losses,
            )

        if self._check_drawdown():
            self._halted = True
            logger.warning(
                "Circuit breaker triggered: drawdown exceeded %.1f%%",
                self._cfg.max_daily_drawdown_pct * 100,
            )

    def reset_circuit_breaker(self) -> None:
        """Manually reset the circuit breaker."""
        self._halted = False
        self._consecutive_losses = 0
        self._daily_pnl = 0.0

    def get_exposure(self, symbol_id: int) -> float:
        """Return current net exposure for a symbol."""
        return self._open_lots.get(symbol_id, 0.0)

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def position_count(self) -> int:
        return self._position_count

    # ── Private checks ─────────────────────────────────────────────────────

    def _check_position_size(self, req: OrderRequest) -> None:
        if req.volume_lots > self._cfg.max_position_lots:
            raise RiskViolation(
                f"Order size {req.volume_lots} lots exceeds max "
                f"({self._cfg.max_position_lots})"
            )

    def _check_symbol_exposure(self, req: OrderRequest) -> None:
        current = self._open_lots.get(req.symbol_id, 0.0)
        if current + req.volume_lots > self._cfg.max_symbol_exposure_lots:
            raise RiskViolation(
                f"Symbol exposure limit ({self._cfg.max_symbol_exposure_lots} lots) "
                f"exceeded (current: {current:.2f}, requested: {req.volume_lots:.2f})"
            )

    def _check_max_positions(self) -> None:
        if self._position_count >= self._cfg.max_open_positions:
            raise RiskViolation(
                f"Max open positions reached ({self._cfg.max_open_positions})"
            )

    def _check_max_order_volume(self, req: OrderRequest) -> None:
        if req.volume_lots > self._cfg.max_order_volume_lots:
            raise RiskViolation(
                f"Order volume {req.volume_lots} exceeds max allowed "
                f"({self._cfg.max_order_volume_lots})"
            )

    def _check_drawdown(self) -> bool:
        """Check if daily drawdown exceeds the configured threshold."""
        if self._initial_equity is None or self._daily_pnl >= 0:
            return False
        drawdown = abs(self._daily_pnl) / self._initial_equity
        return drawdown >= self._cfg.max_daily_drawdown_pct
