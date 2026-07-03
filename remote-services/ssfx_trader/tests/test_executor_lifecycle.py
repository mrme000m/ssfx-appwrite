"""Tests for position lifecycle fixes (SL-hit PnL, volume resolver, backend close)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ssfx_parser import Direction, SignalStatus, SignalType, TradeSignal
from ssfx_trader.backends.simulated import SimulatedBackend
from ssfx_trader.config import PerAccountTradingConfig
from ssfx_trader.executor import TradeExecutor
from ssfx_trader.market_context import MarketContext
from ssfx_trader.risk_monitor import RiskLimits, RiskMonitor
from ssfx_trader.stores.noop_store import NoOpSignalStore
from ssfx_trader.symbol_resolver import SymbolResolver
from ssfx_trader.volume_resolver import VolumeResolver


@dataclass
class _Execution:
    signal_type: str = ""
    status: str = ""
    order_id: int | None = None
    position_id: int | None = None
    executed_price: float | None = None
    volume: float | None = None
    error: str | None = None
    skip_reason: str | None = None


class MemoryAccountStore:
    def __init__(self) -> None:
        self.executions: dict[tuple[str, str, int], _Execution] = {}
        self.risk_states: dict[tuple[str, str], dict[str, Any]] = {}
        self.accounts: dict[str, dict[str, Any]] = {}

    def has_execution(self, slave_id: str, chat_id: str, message_id: int) -> bool:
        return (slave_id, chat_id, message_id) in self.executions

    def get_execution(self, slave_id: str, chat_id: str, message_id: int) -> _Execution | None:
        return self.executions.get((slave_id, chat_id, message_id))

    def update_execution(
        self,
        slave_id: str,
        chat_id: str,
        message_id: int,
        signal_type: str = "",
        status: str = "",
        order_id: int | None = None,
        position_id: int | None = None,
        executed_price: float | None = None,
        volume: float | None = None,
        original_volume_lots: float | None = None,
        error: str | None = None,
        skip_reason: str | None = None,
    ) -> None:
        self.executions[(slave_id, chat_id, message_id)] = _Execution(
            signal_type=signal_type,
            status=status,
            order_id=order_id,
            position_id=position_id,
            executed_price=executed_price,
            volume=volume,
            error=error,
            skip_reason=skip_reason,
        )

    def mark_skipped(self, slave_id: str, chat_id: str, message_id: int, reason: str) -> None:
        self.executions[(slave_id, chat_id, message_id)] = _Execution(
            signal_type="NEW", status="skipped", skip_reason=reason
        )

    def get_risk_state(self, account_name: str, date_str: str) -> dict[str, Any] | None:
        return self.risk_states.get((account_name, date_str))

    def upsert_risk_state(self, account_name: str, state: dict[str, Any]) -> None:
        self.risk_states[(account_name, state.get("date_str", ""))] = state

    def list_accounts(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        return list(self.accounts.values())

    def get_account(self, name: str, owner_id: str | None = None) -> dict[str, Any] | None:
        return self.accounts.get(name)

    def save_account(self, account: dict[str, Any]) -> None:
        self.accounts[account.get("name", "")] = account

    def get_active_executions(self, slave_id: str) -> list[Any]:
        return []

    def list_recent_executions(self, slave_id: str, limit: int = 50) -> list[Any]:
        return []


class FakeMarketContextClient:
    async def get_context(self, symbol: str) -> MarketContext:
        return MarketContext(
            symbol=symbol,
            timestamp_ms=0,
            tick={"bid": 2500.0, "ask": 2500.1, "last": 2500.05},
        )

    async def get_gold_quant(self) -> dict[str, Any]:
        return {}


class FakeSession:
    account_id = 123

    def __init__(self) -> None:
        self.protocol = MagicMock()
        self.protocol.get_trader = AsyncMock(return_value={"balance": 10000.0, "equity": 10000.0})
        self.market_data = MagicMock()
        self.market_data.get_last_price = MagicMock(return_value=2500.0)


class FakeBackend:
    def __init__(self) -> None:
        self._session = FakeSession()

    @property
    def session(self):
        return self._session


class _BackendWithSession(SimulatedBackend):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._session = FakeSession()

    @property
    def session(self):
        return self._session


def _new_signal(
    symbol: str = "XAUUSD",
    direction: Direction = Direction.BUY,
    entry: float = 2500.0,
    sl: float = 2495.0,
    tp1: float = 2510.0,
    message_id: int = 1,
    signal_type: SignalType = SignalType.NEW,
    reply_to_message_id: int | None = None,
) -> TradeSignal:
    return TradeSignal(
        raw_text="test",
        signal_type=signal_type,
        symbol=symbol,
        direction=direction,
        entry_price=entry,
        sl=sl,
        tp1=tp1,
        chat_id="c1",
        message_id=message_id,
        reply_to_message_id=reply_to_message_id,
        timestamp_ms=0,
        status=SignalStatus.EMITTED,
    )


@pytest.fixture
def executor() -> TradeExecutor:
    backend = _BackendWithSession("test")
    trading = PerAccountTradingConfig(
        default_volume=0.01,
        volume_mode="fixed_lots",
        max_positions=10,
        max_positions_per_symbol=10,
        max_open_risk_pct=50.0,
    )
    store = MemoryAccountStore()
    risk_monitor = RiskMonitor(
        account_name="test",
        limits=RiskLimits(
            max_daily_loss_pct=10.0,
            max_drawdown_pct=20.0,
            max_open_risk_pct=50.0,
        ),
        store=store,
    )
    executor = TradeExecutor(
        slave_id="test",
        backend=backend,
        resolver=SymbolResolver(),
        signal_store=NoOpSignalStore(),
        account_store=store,
        trading=trading,
        risk_monitor=risk_monitor,
        market_context_client=FakeMarketContextClient(),
    )
    executor._agent_client._entry_enabled = False
    executor._agent_client._lifecycle_enabled = False
    return executor


@pytest.mark.asyncio
async def test_sl_hit_records_closed_pnl(executor: TradeExecutor) -> None:
    # Open a position first.
    new = _new_signal(message_id=1)
    result = await executor.execute_signal(new)
    assert result["accepted"] is True
    assert executor.active_position_count == 1

    # Simulate a losing close from the broker.
    executor._backend.close_position = AsyncMock(
        return_value={"accepted": True, "realized_pnl": -150.0}
    )

    sl_hit = _new_signal(
        message_id=2,
        signal_type=SignalType.SL_HIT,
        reply_to_message_id=1,
    )
    result = await executor.execute_signal(sl_hit)
    assert result["accepted"] is True
    assert result["code"] == "sl_hit"
    assert executor.active_position_count == 0
    assert executor._risk_monitor._state is not None
    assert executor._risk_monitor._state.daily_pnl == pytest.approx(-150.0)


@pytest.mark.asyncio
async def test_volume_resolver_unwraps_backend_session() -> None:
    """VolumeResolver should reach the real session even when passed a backend wrapper."""
    trading = PerAccountTradingConfig(
        default_volume=0.01,
        volume_mode="percent_risk",
        volume_value=1.0,
        max_volume_lots=1.0,
        min_volume_lots=0.01,
    )
    backend = FakeBackend()
    resolver = VolumeResolver(trading, backend, SymbolResolver())
    signal = _new_signal(entry=2500.0, sl=2495.0)
    volume = await resolver.resolve_volume(signal)
    assert volume > 0
    backend._session.protocol.get_trader.assert_awaited_once()


@pytest.mark.asyncio
async def test_volume_resolver_falls_back_when_no_session() -> None:
    trading = PerAccountTradingConfig(
        default_volume=0.05,
        volume_mode="percent_risk",
        volume_value=1.0,
        max_volume_lots=1.0,
        min_volume_lots=0.01,
    )
    resolver = VolumeResolver(trading, None, SymbolResolver())  # type: ignore[arg-type]
    signal = _new_signal(entry=2500.0, sl=2495.0)
    volume = await resolver.resolve_volume(signal)
    assert volume == pytest.approx(0.01)
