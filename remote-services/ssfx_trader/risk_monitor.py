"""Per-account risk kill-switches for daily loss, drawdown, and panic stop."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .stores.base import AccountStore

logger = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    """User-configured risk thresholds for one account."""

    max_daily_loss_pct: float | None = None
    max_drawdown_pct: float | None = None
    max_open_risk_pct: float | None = None
    panic_stop: bool = False
    risk_reset_utc_hour: int = 0


@dataclass
class RiskState:
    """Mutable runtime state persisted to Appwrite after each material change."""

    date_str: str
    daily_start_equity: float = 0.0
    daily_pnl: float = 0.0
    peak_equity: float = 0.0
    kill_switch_active: bool = False
    kill_switch_reason: str | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "date_str": self.date_str,
            "daily_start_equity": self.daily_start_equity,
            "daily_pnl": self.daily_pnl,
            "peak_equity": self.peak_equity,
            "kill_switch_active": self.kill_switch_active,
            "kill_switch_reason": self.kill_switch_reason,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RiskState":
        return cls(
            date_str=data.get("date_str", ""),
            daily_start_equity=float(data.get("daily_start_equity", 0.0)),
            daily_pnl=float(data.get("daily_pnl", 0.0)),
            peak_equity=float(data.get("peak_equity", 0.0)),
            kill_switch_active=bool(data.get("kill_switch_active", False)),
            kill_switch_reason=data.get("kill_switch_reason"),
            updated_at=data.get("updated_at", datetime.now(UTC).isoformat()),
        )


class RiskMonitor:
    """Tracks daily PnL / peak equity and trips kill-switches for one account."""

    def __init__(
        self,
        account_name: str,
        limits: RiskLimits,
        store: AccountStore,
    ):
        self._account_name = account_name
        self._limits = limits
        self._store = store
        self._state: RiskState | None = None

    @staticmethod
    def _today_str() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def _persist(self) -> None:
        if self._state is None:
            return
        self._state.updated_at = datetime.now(UTC).isoformat()
        try:
            self._store.upsert_risk_state(self._account_name, self._state.to_dict())
        except Exception:
            logger.exception("[%s] Failed to persist risk state", self._account_name)

    def _should_reset(self, state: RiskState) -> bool:
        """True if the configured UTC reset hour has passed since the last update."""
        try:
            last = datetime.fromisoformat(state.updated_at)
        except Exception:
            return False

        now = datetime.now(UTC)
        reset_hour = self._limits.risk_reset_utc_hour % 24
        last_reset = last.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
        if last >= last_reset:
            last_reset += timedelta(days=1)
        return now >= last_reset

    def load(self, equity: float) -> None:
        today = self._today_str()
        row = self._store.get_risk_state(self._account_name, today)

        if row is None:
            self._state = RiskState(
                date_str=today,
                daily_start_equity=equity,
                peak_equity=equity,
            )
            self._persist()
        else:
            state = RiskState.from_dict(row)
            if state.date_str != today or self._should_reset(state):
                self._state = RiskState(
                    date_str=today,
                    daily_start_equity=equity,
                    peak_equity=equity,
                    daily_pnl=0.0,
                )
                self._persist()
            else:
                self._state = state

    def check_new_signal(self, equity: float, open_risk_pct: float | None = None) -> tuple[bool, str]:
        """Return (allowed, reason). Must be called before opening a NEW position.
        
        Args:
            equity: Current account equity
            open_risk_pct: Estimated % of equity at risk for the new position (optional).
                          If provided and max_open_risk_pct is set, checks against the limit.
        """
        if self._limits.panic_stop:
            logger.error("[%s] Risk kill-switch: panic_stop active", self._account_name)
            return False, "panic_stop_active"

        today = self._today_str()
        if self._state is None or self._state.date_str != today:
            self.load(equity)

        state = self._state
        assert state is not None

        self._update_peak(equity)

        if state.kill_switch_active:
            return False, f"kill_switch_active:{state.kill_switch_reason}"

        if self._limits.max_daily_loss_pct is not None and self._limits.max_daily_loss_pct > 0:
            threshold = state.daily_start_equity * (self._limits.max_daily_loss_pct / 100.0)
            if state.daily_pnl <= -threshold:
                self._trip(f"daily_loss_{self._limits.max_daily_loss_pct}%")
                return False, state.kill_switch_reason or "daily_loss_limit"

        if self._limits.max_drawdown_pct is not None and self._limits.max_drawdown_pct > 0:
            if state.peak_equity > 0:
                drawdown = (state.peak_equity - equity) / state.peak_equity
                if drawdown >= self._limits.max_drawdown_pct / 100.0:
                    self._trip(f"drawdown_{self._limits.max_drawdown_pct}%")
                    return False, state.kill_switch_reason or "drawdown_limit"

        if self._limits.max_open_risk_pct is not None and self._limits.max_open_risk_pct > 0:
            if open_risk_pct is not None:
                if open_risk_pct >= self._limits.max_open_risk_pct:
                    self._trip(f"max_open_risk_{self._limits.max_open_risk_pct}%")
                    return False, state.kill_switch_reason or "max_open_risk_limit"

        return True, ""

    def _update_peak(self, equity: float) -> None:
        state = self._state
        assert state is not None
        if state.peak_equity == 0.0 or equity > state.peak_equity:
            state.peak_equity = equity
            self._persist()

    def _trip(self, reason: str) -> None:
        state = self._state
        assert state is not None
        state.kill_switch_active = True
        state.kill_switch_reason = reason
        self._persist()
        logger.error("[%s] Risk kill-switch tripped: %s", self._account_name, reason)

    def record_closed_pnl(self, closed_pnl: float, equity: float) -> None:
        """Call after a position closes to accumulate realized daily PnL."""
        today = self._today_str()
        if self._state is None or self._state.date_str != today:
            self.load(equity)
        state = self._state
        assert state is not None
        state.daily_pnl += closed_pnl
        self._update_peak(equity)
        self._persist()
        logger.info(
            "[%s] Recorded closed PnL %.2f; daily_pnl=%.2f peak_equity=%.2f",
            self._account_name,
            closed_pnl,
            state.daily_pnl,
            state.peak_equity,
        )
