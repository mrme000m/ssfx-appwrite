"""Tests for the admin API config update endpoint."""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException, Request

from ssfx_server import admin_api
from ssfx_server.tests.test_webhook_auth import _FakeState


class _FakeStore:
    def __init__(self, account: dict[str, Any] | None = None):
        self._account = account or {
            "name": "demo",
            "enabled": True,
            "ctrader": {"host_type": "demo"},
            "trading": {"execution_mode": "demo"},
            "symbols_filter": [],
        }
        self.saved: dict[str, Any] | None = None

    def get_account(self, name: str) -> dict[str, Any] | None:
        return self._account if name == self._account["name"] else None

    def list_accounts(self) -> list[dict[str, Any]]:
        return [dict(self._account)]

    def save_account(self, doc: dict[str, Any]) -> None:
        self.saved = dict(doc)


class _FakeUpdateRequest(Request):
    def __init__(self, payload: dict[str, Any], admin_key: str = "secret") -> None:
        scope = {
            "type": "http",
            "method": "PATCH",
            "headers": [(b"x-admin-key", admin_key.encode())],
            "path": "/api/accounts/demo",
            "query_string": b"",
        }
        super().__init__(scope)
        self._payload = payload

    async def json(self) -> dict[str, Any]:
        return self._payload


@pytest.mark.asyncio
async def test_update_account_valid_config() -> None:
    store = _FakeStore()
    state = _FakeState(admin_api_key="secret")
    state.account_store = store  # type: ignore[attr-defined]
    admin_api.set_state(state)

    req = _FakeUpdateRequest({"config": {"trading": {"execution_mode": "live"}}})
    resp = await admin_api.update_account("demo", req)
    assert resp.body == b'{"ok":true,"name":"demo","enabled":true}'
    assert store.saved is not None
    assert store.saved["trading"]["execution_mode"] == "live"


@pytest.mark.asyncio
async def test_update_account_invalid_enum_rejected() -> None:
    store = _FakeStore()
    state = _FakeState(admin_api_key="secret")
    state.account_store = store  # type: ignore[attr-defined]
    admin_api.set_state(state)

    req = _FakeUpdateRequest({"config": {"trading": {"execution_mode": "not_a_mode"}}})
    with pytest.raises(HTTPException) as exc_info:
        await admin_api.update_account("demo", req)
    assert exc_info.value.status_code == 400
    assert "Invalid account config" in exc_info.value.detail


@pytest.mark.asyncio
async def test_update_account_not_found() -> None:
    state = _FakeState(admin_api_key="secret")
    state.account_store = _FakeStore()  # type: ignore[attr-defined]
    admin_api.set_state(state)

    req = _FakeUpdateRequest({"config": {"trading": {"execution_mode": "live"}}})
    with pytest.raises(HTTPException) as exc_info:
        await admin_api.update_account("missing", req)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_accounts_redacts_secrets() -> None:
    store = _FakeStore(
        account={
            "name": "demo",
            "enabled": True,
            "ctrader": {
                "client_id": "cid",
                "client_secret": "super-secret",
                "host_type": "demo",
            },
            "trading": {"execution_mode": "demo"},
            "symbols_filter": [],
        }
    )
    state = _FakeState(admin_api_key="secret")
    state.account_store = store  # type: ignore[attr-defined]
    admin_api.set_state(state)

    req = _FakeUpdateRequest({})
    resp = await admin_api.list_accounts(req)
    data = json.loads(resp.body)
    assert len(data) == 1
    ctrader = data[0]["config"]["ctrader"]
    assert ctrader["client_id"] == "***"
    assert ctrader["client_secret"] == "***"
