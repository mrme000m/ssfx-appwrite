"""Unit tests for per-account risk kill-switches."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ssfx_trader.risk_monitor import RiskLimits, RiskMonitor, RiskState


class _MemoryStore:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    def get_risk_state(self, account_name: str, date_str: str) -> dict | None:
        return self.rows.get(f"{account_name}:{date_str}")

    def upsert_risk_state(self, account_name: str, state: dict) -> None:
        self.rows[f"{account_name}:{state['date_str']}"] = dict(state)


def _monitor(limits: RiskLimits | None = None, store: _MemoryStore | None = None) -> RiskMonitor:
    return RiskMonitor(
        account_name="test_account",
        limits=limits or RiskLimits(),
        store=store or _MemoryStore(),
    )


def test_panic_stop_blocks_new_signal():
    store = _MemoryStore()
    monitor = _monitor(RiskLimits(panic_stop=True), store)
    allowed, reason = monitor.check_new_signal(equity=10000.0)
    assert allowed is False
    assert reason == "panic_stop_active"


def test_daily_loss_limit_trips_kill_switch():
    store = _MemoryStore()
    monitor = _monitor(RiskLimits(max_daily_loss_pct=2.0), store)

    # First signal on a fresh day is allowed.
    allowed, _ = monitor.check_new_signal(equity=10000.0)
    assert allowed is True

    # Simulate a realized loss of $300 (3% of equity).
    monitor.record_closed_pnl(-300.0, equity=9700.0)

    # Next signal should trip the kill-switch.
    allowed, reason = monitor.check_new_signal(equity=9700.0)
    assert allowed is False
    assert "daily_loss" in reason
    assert monitor._state.kill_switch_active is True


def test_drawdown_limit_trips_kill_switch():
    store = _MemoryStore()
    monitor = _monitor(RiskLimits(max_drawdown_pct=5.0), store)

    allowed, _ = monitor.check_new_signal(equity=10000.0)
    assert allowed is True

    # Equity drops 6% from peak.
    allowed, reason = monitor.check_new_signal(equity=9400.0)
    assert allowed is False
    assert "drawdown" in reason


def test_state_persists_and_loads():
    store = _MemoryStore()
    monitor = _monitor(RiskLimits(max_daily_loss_pct=1.0), store)
    monitor.check_new_signal(equity=5000.0)
    monitor.record_closed_pnl(-100.0, equity=4900.0)

    # New monitor instance loads persisted state.
    monitor2 = _monitor(RiskLimits(max_daily_loss_pct=1.0), store)
    monitor2.load(equity=4900.0)
    assert monitor2._state.daily_pnl == pytest.approx(-100.0)
    assert monitor2._state.peak_equity == pytest.approx(5000.0)


def test_state_reset_on_new_day():
    yesterday = "2026-07-01"
    today = "2026-07-02"

    store = _MemoryStore()
    store.rows["acct:yesterday"] = RiskState(
        date_str=yesterday,
        daily_start_equity=10000.0,
        daily_pnl=-250.0,
        peak_equity=10000.0,
        kill_switch_active=True,
        kill_switch_reason="daily_loss_1%",
        updated_at=datetime.now(UTC).isoformat(),
    ).to_dict()

    monitor = _monitor(RiskLimits(max_daily_loss_pct=1.0), store)
    # Force today string; the monitor normally uses UTC date.
    monitor._today_str = lambda: today  # type: ignore[method-assign]
    monitor.load(equity=9500.0)

    assert monitor._state.date_str == today
    assert monitor._state.daily_pnl == pytest.approx(0.0)
    assert monitor._state.kill_switch_active is False


def test_peak_equity_tracks_watermark():
    store = _MemoryStore()
    monitor = _monitor(RiskLimits(), store)
    monitor.check_new_signal(equity=10000.0)
    monitor.check_new_signal(equity=10500.0)
    assert monitor._state.peak_equity == pytest.approx(10500.0)


def test_max_open_risk_pct_blocks_high_risk_signal():
    store = _MemoryStore()
    monitor = _monitor(RiskLimits(max_open_risk_pct=5.0), store)

    # Signal with 6% risk should be blocked
    allowed, reason = monitor.check_new_signal(equity=10000.0, open_risk_pct=6.0)
    assert allowed is False
    assert "max_open_risk" in reason
    assert monitor._state.kill_switch_active is True


def test_max_open_risk_pct_allows_low_risk_signal():
    store = _MemoryStore()
    monitor = _monitor(RiskLimits(max_open_risk_pct=5.0), store)

    # Signal with 3% risk should be allowed
    allowed, reason = monitor.check_new_signal(equity=10000.0, open_risk_pct=3.0)
    assert allowed is True
    assert reason == ""


def test_max_open_risk_pct_ignored_when_not_set():
    store = _MemoryStore()
    monitor = _monitor(RiskLimits(max_open_risk_pct=None), store)

    # Signal with high risk should be allowed when no limit is set
    allowed, reason = monitor.check_new_signal(equity=10000.0, open_risk_pct=50.0)
    assert allowed is True


def test_max_open_risk_pct_ignored_when_no_risk_value():
    store = _MemoryStore()
    monitor = _monitor(RiskLimits(max_open_risk_pct=5.0), store)

    # Signal without open_risk_pct should be allowed (backward compatible)
    allowed, reason = monitor.check_new_signal(equity=10000.0)
    assert allowed is True
