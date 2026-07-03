"""Tests for the autonomous gold-quant signal generator."""
from __future__ import annotations

from typing import Any

import pytest

from ssfx_server.signal_generator import GoldQuantGeneratorConfig, GoldQuantSignalGenerator


class _FakeConfig:
    def __init__(self) -> None:
        self.name = "f1"
        self.enabled = True
        self.symbols_filter: list[str] = []

    def allows_symbol(self, symbol: str | None) -> bool:
        return True


class _FakeExecutor:
    def __init__(self) -> None:
        self._trading = type(
            "T",
            (),
            {
                "max_positions": 10,
                "max_positions_per_symbol": 10,
                "allow_same_symbol_add": False,
                "allow_opposite_direction": False,
            },
        )()
        self._active_positions: dict[str, Any] = {}
        self._risk_monitor = None

    @property
    def active_position_count(self) -> int:
        return len(self._active_positions)


class _FakeFollower:
    def __init__(self) -> None:
        self.received: list[Any] = []
        self._config = _FakeConfig()
        self._executor = _FakeExecutor()

    async def on_signal(self, signal: Any) -> None:
        self.received.append(signal)


class _FakeAgentClient:
    def __init__(self, action: str = "ENTER", confidence: float = 0.9) -> None:
        self.action = action
        self.confidence = confidence

    async def entry_decision(self, **kwargs: Any) -> dict[str, Any]:
        return {"decision": {"action": self.action, "confidence": self.confidence}}


@pytest.fixture
def generator(monkeypatch: pytest.MonkeyPatch) -> GoldQuantSignalGenerator:
    config = GoldQuantGeneratorConfig(enabled=True, interval_sec=1.0)
    gen = GoldQuantSignalGenerator(
        config=config,
        data_service_base_url="http://test",
        data_service_api_key=None,
        agent_harness_base_url="http://test",
        followers={"f1": _FakeFollower()},
    )
    # Patch the real HTTP client with a fake.
    monkeypatch.setattr(gen, "_agent_client", _FakeAgentClient())
    return gen


@pytest.mark.asyncio
async def test_generator_skips_when_quant_not_enter(generator: GoldQuantSignalGenerator) -> None:
    async def _fetch() -> dict[str, Any]:
        return {"decision": {"long_entry": {"verdict": "WAIT", "confidence": 0.9}}}

    generator._fetch_snapshot = _fetch
    await generator._evaluate_once()
    assert generator._followers["f1"].received == []


@pytest.mark.asyncio
async def test_generator_skips_low_quant_confidence(generator: GoldQuantSignalGenerator) -> None:
    generator._config.min_quant_confidence = 0.9

    async def _fetch() -> dict[str, Any]:
        return {"decision": {"long_entry": {"verdict": "ENTER", "confidence": 0.5}}}

    generator._fetch_snapshot = _fetch
    await generator._evaluate_once()
    assert generator._followers["f1"].received == []


@pytest.mark.asyncio
async def test_generator_injects_signal_on_enter(generator: GoldQuantSignalGenerator) -> None:
    async def _fetch() -> dict[str, Any]:
        return {
            "tick": {"last": 2500.0, "bid": 2499.9, "ask": 2500.1},
            "decision": {"long_entry": {"verdict": "ENTER", "confidence": 0.9}},
        }

    generator._fetch_snapshot = _fetch
    await generator._evaluate_once()
    assert len(generator._followers["f1"].received) == 1
    signal = generator._followers["f1"].received[0]
    assert signal.symbol == "XAUUSD"
    assert signal.direction.value == "BUY"


@pytest.mark.asyncio
async def test_generator_skips_when_agent_rejects(
    generator: GoldQuantSignalGenerator, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generator, "_agent_client", _FakeAgentClient(action="REJECT", confidence=0.9))

    async def _fetch() -> dict[str, Any]:
        return {
            "tick": {"last": 2500.0},
            "decision": {"long_entry": {"verdict": "ENTER", "confidence": 0.9}},
        }

    generator._fetch_snapshot = _fetch
    await generator._evaluate_once()
    assert generator._followers["f1"].received == []
