"""Tests for CTraderBackend restart-resilience helpers."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncio

import pytest

from ssfx_trader.backends.ctrader import CTraderBackend
from ssfx_trader.config import CTraderConfig


def _make_position(position_id: int, symbol_id: int):
    pos = MagicMock()
    pos.positionId = position_id
    trade_data = MagicMock()
    trade_data.symbolId = symbol_id
    pos.tradeData = trade_data
    return pos


@pytest.mark.asyncio
async def test_close_position_resolves_symbol_id_from_reconcile() -> None:
    """If the local symbol map is lost, close_position fetches it from the broker."""
    cfg = CTraderConfig(grant_id="g", client_id="c", client_secret="s", account_id=123)
    backend = CTraderBackend("test", cfg)

    session = MagicMock()
    session.account_id = 123

    reconcile_response = MagicMock()
    reconcile_response.position = [_make_position(position_id=42, symbol_id=5)]
    session.protocol.reconcile = AsyncMock(return_value=reconcile_response)

    close_response = MagicMock()
    close_response.errorCode = None
    fut = asyncio.Future()
    fut.set_result(close_response)
    session.execution.close_position = AsyncMock(return_value=fut)

    backend._session = session  # type: ignore[assignment]

    result = await backend.close_position(42, volume_lots=None)
    assert result.get("accepted") is True
    session.protocol.reconcile.assert_awaited_once()
    session.execution.close_position.assert_awaited_once()
    # The local cache is intentionally cleared after a full close.
    assert 42 not in backend._position_symbols
