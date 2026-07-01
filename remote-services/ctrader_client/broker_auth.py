"""Broker authentication — delegates OAuth lifecycle to the Cloudflare auth broker."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

import aiohttp

from .auth import TokenData

logger = logging.getLogger(__name__)

# cTrader accountType enum mapping
_ACCOUNT_TYPE_MAP: dict[int, str] = {
    0: "HEDGED",
    1: "NETTED",
    2: "SPREAD_BETTING",
}


async def sync_accounts_to_broker(
    protocol: Any,
    access_token: str,
    broker_url: str,
    grant_id: str,
    internal_api_key: str,
    selected_account_id: int | None = None,
) -> list[dict[str, Any]]:
    """Enrich accounts from cTrader API and POST to the auth broker.

    Calls get_accounts() to list, then get_trader() per account for rich
    fields (balance, brokerName, accountType, leverageInCents, etc.),
    and POSTs the enriched data to POST /internal/grant/:grant_id/accounts.
    """
    broker = broker_url.rstrip("/")

    if not access_token:
        logger.warning("sync_accounts_to_broker: no access token available")
        return []

    try:
        res = await protocol.get_accounts(access_token)
    except Exception as exc:
        logger.warning("sync_accounts_to_broker: get_accounts failed: %s", exc)
        return []

    accounts = getattr(res, "ctidTraderAccount", None)
    if accounts is None:
        accounts = getattr(res, "account", None)
    if accounts is None:
        logger.debug("sync_accounts_to_broker: no accounts in response")
        return []

    rich_accounts: list[dict[str, Any]] = []
    for acc in list(accounts):
        acc_id = getattr(acc, "ctidTraderAccountId", None)
        if acc_id is None:
            continue
        entry: dict[str, Any] = {
            "ctidTraderAccountId": acc_id,
            "isLive": getattr(acc, "isLive", False),
            "traderLogin": getattr(acc, "traderLogin", ""),
            "lastClosingDealTimestamp": getattr(acc, "lastClosingDealTimestamp", None),
            "lastBalanceUpdateTimestamp": getattr(acc, "lastBalanceUpdateTimestamp", None),
            "selected": selected_account_id is not None and acc_id == selected_account_id,
        }

        try:
            trader = await protocol.get_trader(acc_id)
        except Exception as exc:
            logger.warning("sync_accounts_to_broker: get_trader(%s) failed: %s", acc_id, exc)
            rich_accounts.append(entry)
            continue

        nested = getattr(trader, "trader", None)
        if nested is not None:
            nested_balance = getattr(nested, "balance", None)
            if isinstance(nested_balance, (int, float)) and not isinstance(nested_balance, bool):
                trader = nested

        entry["brokerName"] = getattr(trader, "brokerName", "")
        entry["brokerTitleShort"] = getattr(acc, "brokerTitleShort", "") or getattr(trader, "brokerTitleShort", "")
        acc_type_raw = getattr(trader, "accountType", None)
        if isinstance(acc_type_raw, int):
            entry["accountType"] = _ACCOUNT_TYPE_MAP.get(acc_type_raw, str(acc_type_raw))
        elif acc_type_raw is not None:
            entry["accountType"] = str(acc_type_raw)
        else:
            entry["accountType"] = ""
        entry["balance"] = getattr(trader, "balance", 0)
        entry["moneyDigits"] = getattr(trader, "moneyDigits", 0)
        entry["depositAssetId"] = getattr(trader, "depositAssetId", 0)
        entry["leverageInCents"] = getattr(trader, "leverageInCents", 0)
        entry["registrationTimestamp"] = getattr(trader, "registrationTimestamp", None)

        rich_accounts.append(entry)

    try:
        payload: dict[str, Any] = {"accounts": rich_accounts}
        if selected_account_id is not None:
            payload["selectedAccountId"] = str(selected_account_id)
        async with aiohttp.ClientSession() as session, session.post(
            f"{broker}/internal/grant/{grant_id}/accounts",
            json=payload,
            headers={"x-internal-key": internal_api_key},
        ) as resp:
            body = await resp.json()
            if resp.status != 200:
                logger.warning(
                    "sync_accounts_to_broker: POST accounts failed HTTP %s: %s",
                    resp.status, body,
                )
            else:
                logger.info(
                    "sync_accounts_to_broker: synced %d accounts for grant %s",
                    len(rich_accounts), grant_id,
                )
    except Exception as exc:
        logger.warning("sync_accounts_to_broker: POST to broker failed: %s", exc)

    return rich_accounts


class BrokerTokenManager:
    """Delegates token lifecycle to the Cloudflare auth broker.

    The broker handles:
    - Refresh token storage (encrypted at rest)
    - Token refresh via Durable Object locking

    The Python client only receives short-lived access tokens and
    an opaque grant_id.  client_secret and refresh_token never leave
    the broker.
    """

    def __init__(self, broker_url: str, grant_id: str, internal_api_key: str | None = None):
        self._broker = broker_url.rstrip("/")
        self._grant_id = grant_id
        self._internal_api_key = internal_api_key
        self._token_data: TokenData | None = None
        self._refresh_task: asyncio.Task[None] | None = None

    @property
    def access_token(self) -> str:
        if self._token_data is None:
            raise RuntimeError("No tokens loaded — refresh first")
        return self._token_data.access_token

    @property
    def has_tokens(self) -> bool:
        return self._token_data is not None

    # ── Token refresh ──────────────────────────────────────────────────────

    async def refresh(self) -> TokenData:
        """Refresh access token via the auth broker."""
        headers: dict[str, str] = {}
        if self._internal_api_key:
            headers["x-internal-key"] = self._internal_api_key
        async with aiohttp.ClientSession() as session, session.post(
            f"{self._broker}/internal/ctrader/refresh",
            json={"grantId": self._grant_id},
            headers=headers,
        ) as resp:
            body: dict[str, Any] = await resp.json()
            if resp.status != 200:
                raise RuntimeError(
                    f"Broker refresh failed: HTTP {resp.status} body={body}"
                )

        access_token = body.get("access_token")
        if not access_token:
            raise RuntimeError(
                f"Broker refresh failed: missing access_token in body={body}"
            )

        expires_at_raw = body.get("expires_at", time.time() + 2_628_000)
        if isinstance(expires_at_raw, str):
            from datetime import UTC, datetime

            dt = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            expires_at = dt.timestamp()
        else:
            expires_at = float(expires_at_raw)
        self._token_data = TokenData(
            access_token=access_token,
            refresh_token="",  # Never stored client-side
            expires_at=expires_at,
        )
        logger.info("Token refreshed via broker, expires_at=%s", expires_at)
        return self._token_data

    # ── Expiry check ───────────────────────────────────────────────────────

    def is_expired(self, buffer_seconds: float = 300) -> bool:
        """Check if the access token is expired (with safety buffer)."""
        if self._token_data is None:
            return True
        return time.time() + buffer_seconds >= self._token_data.expires_at

    # ── Auto-refresh ───────────────────────────────────────────────────────

    async def start_auto_refresh(self, refresh_ahead_seconds: float = 3600) -> None:
        """Start background task that refreshes tokens before expiry."""
        async def _loop() -> None:
            while True:
                if self._token_data is None:
                    await asyncio.sleep(60)
                    continue
                sleep_time = max(
                    0,
                    self._token_data.expires_at - time.time() - refresh_ahead_seconds,
                )
                if sleep_time > 0:
                    logger.info("Broker token refresh scheduled in %.0fs", sleep_time)
                    await asyncio.sleep(sleep_time)
                try:
                    await self.refresh()
                except Exception as exc:
                    logger.error("Broker token auto-refresh failed: %s", exc)
                    await asyncio.sleep(60)

        self._refresh_task = asyncio.create_task(_loop())

    async def stop_auto_refresh(self) -> None:
        """Cancel the auto-refresh background task."""
        if self._refresh_task:
            self._refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._refresh_task
            self._refresh_task = None
