"""Integration tests for TradeExecutor using SimulatedBackend."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from ssfx_parser import Direction, OrderType, SignalStatus, SignalType, TradeSignal
from ssfx_trader.backends.simulated import SimulatedBackend
from ssfx_trader.config import PerAccountTradingConfig
from ssfx_trader.executor import TradeExecutor
from ssfx_trader.market_context import MarketContext
from ssfx_trader.risk_monitor import RiskLimits, RiskMonitor
from ssfx_trader.stores.noop_store import NoOpSignalStore
from ssfx_trader.symbol_resolver import SymbolResolver


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
    """Simple in-memory account store for tests."""

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


def _new_signal(
    symbol: str = "XAUUSD",
    direction: Direction = Direction.BUY,
    entry: float = 2500.0,
    sl: float = 2495.0,
    tp1: float = 2510.0,
    message_id: int = 1,
) -> TradeSignal:
    return TradeSignal(
        raw_text="test",
        signal_type=SignalType.NEW,
        symbol=symbol,
        direction=direction,
        entry_price=entry,
        sl=sl,
        tp1=tp1,
        chat_id="c1",
        message_id=message_id,
        timestamp_ms=0,
        status=SignalStatus.EMITTED,
    )


@pytest.fixture
def executor() -> TradeExecutor:
    backend = SimulatedBackend("test")
    trading = PerAccountTradingConfig(
        default_volume=0.01,
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
    executor._agent_client._entry_enabled = True
    return executor


@pytest.mark.asyncio
async def test_agent_wait_skips_order(executor: TradeExecutor) -> None:
    async def _wait(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"decision": {"action": "WAIT", "confidence": 0.8, "reasons": ["bad timing"]}}

    executor._agent_client.entry_decision = _wait

    result = await executor.execute_signal(_new_signal())
    assert result["accepted"] is False
    assert result["code"] == "agent_wait"
    assert executor.active_position_count == 0


@pytest.mark.asyncio
async def test_agent_modify_limit_sets_order_type(executor: TradeExecutor) -> None:
    async def _modify(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "decision": {
                "action": "MODIFY",
                "confidence": 0.9,
                "limit_price": 2498.0,
                "sl": 2495.0,
                "tp1": 2510.0,
            }
        }

    executor._agent_client.entry_decision = _modify

    sig = _new_signal(entry=2500.0)
    result = await executor.execute_signal(sig)
    assert result["accepted"] is True
    assert result["execution"]["order_type"] == "LIMIT"
    assert sig.order_type == OrderType.LIMIT
    assert sig.entry_price == 2498.0
    assert executor.active_position_count == 1


@pytest.mark.asyncio
async def test_max_open_risk_pct_blocks_signal(executor: TradeExecutor) -> None:
    # Set an extremely low open-risk limit so a 0.01-lot XAUUSD signal exceeds it.
    executor._trading.max_open_risk_pct = 0.05
    executor._risk_monitor._limits.max_open_risk_pct = 0.05
    executor._agent_client._entry_enabled = False

    # 5 USD SL distance * 0.01 lots * 100 lot_size = 5 USD risk / 10000 equity = 0.05%
    sig = _new_signal(entry=2500.0, sl=2495.0)
    result = await executor.execute_signal(sig)
    assert result["accepted"] is False
    assert result["code"] == "risk_kill_switch"
