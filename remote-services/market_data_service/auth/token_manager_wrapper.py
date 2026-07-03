"""Unified token management supporting both direct and broker-based auth."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class AuthManager:
    """Manages authentication lifecycle with support for multiple token modes.

    Supports:
    - AppwriteTokenManager: Appwrite api-internal function (preferred)
    - BrokerTokenManager: Cloudflare auth broker (legacy)
    - TokenManager: Direct OAuth token management (legacy)
    - Raw tokens: Fallback for direct token provision (for backward compatibility)
    """

    def __init__(
        self,
        broker_url: str | None = None,
        grant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        internal_api_key: str | None = None,
        appwrite_mode: bool = False,
    ):
        """Initialize auth manager.

        Args:
            broker_url: Auth broker / api-internal URL
            grant_id: Grant ID from auth broker
            client_id: cTrader client ID (for direct mode)
            client_secret: cTrader client secret (for direct mode)
            access_token: Raw access token (fallback mode)
            refresh_token: Raw refresh token (fallback mode)
            internal_api_key: Internal API key for Appwrite/broker mode
            appwrite_mode: If True, use AppwriteTokenManager instead of BrokerTokenManager
        """
        self._broker_url = broker_url
        self._grant_id = grant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._internal_api_key = internal_api_key
        self._appwrite_mode = appwrite_mode

        self._token_mgr: Any = None
        self._mode: str = "unknown"
        self._refresh_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        """Initialize the appropriate token manager based on configuration."""
        if self._broker_url and self._grant_id:
            if self._appwrite_mode:
                await self._init_appwrite_mode()
            else:
                await self._init_broker_mode()
        elif self._client_id and self._client_secret:
            await self._init_direct_mode()
        else:
            self._init_raw_mode()

    async def _init_appwrite_mode(self) -> None:
        """Initialize AppwriteTokenManager for Appwrite-based auth."""
        try:
            from ctrader_client.appwrite_auth import AppwriteTokenManager

            self._token_mgr = AppwriteTokenManager(
                internal_url=self._broker_url,
                grant_id=self._grant_id,
                internal_api_key=self._internal_api_key or "",
            )
            await self._token_mgr.refresh()
            self._mode = "appwrite"
            logger.info(
                "Auth initialized in appwrite mode (url=%s, grant_id=%s)",
                self._broker_url, self._grant_id,
            )
        except ImportError:
            logger.warning("AppwriteTokenManager not available, falling back to broker mode")
            await self._init_broker_mode()
        except Exception as exc:
            logger.error("Failed to initialize appwrite auth: %s", exc)
            raise

    async def _init_broker_mode(self) -> None:
        """Initialize BrokerTokenManager for broker-based auth."""
        try:
            from ctrader_client.broker_auth import BrokerTokenManager

            self._token_mgr = BrokerTokenManager(
                broker_url=self._broker_url,
                grant_id=self._grant_id,
            )
            await self._token_mgr.refresh()
            self._mode = "broker"
            logger.info(
                "Auth initialized in broker mode (broker_url=%s, grant_id=%s)",
                self._broker_url,
                self._grant_id,
            )
        except ImportError:
            logger.warning("BrokerTokenManager not available, falling back to raw mode")
            self._init_raw_mode()
        except Exception as exc:
            logger.error("Failed to initialize broker auth: %s", exc)
            raise

    async def _init_direct_mode(self) -> None:
        """Initialize TokenManager for direct OAuth management."""
        try:
            from ctrader_client.auth import TokenManager, TokenData

            self._token_mgr = TokenManager(
                client_id=self._client_id,
                client_secret=self._client_secret,
                token_data=TokenData(
                    access_token=self._access_token or "",
                    refresh_token=self._refresh_token or "",
                    expires_at=time.time() + 3600,
                ),
            )
            if self._refresh_token:
                await self._token_mgr.refresh()
                self._mode = "direct"
                logger.info("Auth initialized in direct mode (TokenManager)")
            elif self._access_token:
                # No refresh token, but we have an access token — use it without refresh
                self._mode = "direct-no-refresh"
                logger.info(
                    "Auth initialized in direct mode (TokenManager, no refresh token) "
                    "access_token will be used until expiry"
                )
            else:
                raise ValueError("client_id and client_secret provided but no access_token")
        except ImportError:
            logger.warning("TokenManager not available, using raw tokens")
            self._init_raw_mode()
        except Exception as exc:
            logger.error("Failed to initialize direct auth: %s", exc)
            raise

    def _init_raw_mode(self) -> None:
        """Initialize raw token mode (no refresh capability)."""
        if not self._access_token:
            raise ValueError(
                "No authentication credentials provided: "
                "need either broker_url+grant_id, client_id+client_secret, or access_token"
            )
        self._mode = "raw"
        logger.warning(
            "Auth initialized in raw mode (no token refresh) — tokens may expire"
        )

    @property
    def access_token(self) -> str:
        """Get the current access token."""
        if self._mode == "raw":
            if not self._access_token:
                raise RuntimeError("No access token available")
            return self._access_token
        if self._token_mgr is None:
            raise RuntimeError("Auth not initialized")
        return self._token_mgr.access_token

    @property
    def is_expired(self) -> bool:
        """Check if token is expired (with 5-minute buffer)."""
        if self._mode == "raw":
            return False  # Can't check expiration in raw mode
        if self._token_mgr is None:
            return True
        return self._token_mgr.is_expired(buffer_seconds=300)

    async def refresh_token(self) -> None:
        """Manually refresh the access token."""
        if self._mode == "raw":
            logger.warning("Cannot refresh tokens in raw mode")
            return
        if self._token_mgr is None:
            raise RuntimeError("Auth not initialized")
        await self._token_mgr.refresh()

    async def start_auto_refresh(self, refresh_ahead_seconds: float = 3600) -> None:
        """Start automatic token refresh task.

        Args:
            refresh_ahead_seconds: Refresh token this many seconds before expiration
        """
        if self._mode == "raw":
            logger.debug("Auto-refresh not available in raw mode")
            return
        if self._token_mgr is None:
            raise RuntimeError("Auth not initialized")

        if self._refresh_task is not None:
            logger.debug("Auto-refresh already running")
            return

        if hasattr(self._token_mgr, "start_auto_refresh"):
            # For BrokerTokenManager or TokenManager with auto-refresh support
            await self._token_mgr.start_auto_refresh(refresh_ahead_seconds)
            self._refresh_task = getattr(self._token_mgr, "_refresh_task", None)
            logger.info("Auto-refresh started (refresh_ahead=%ds)", refresh_ahead_seconds)
        else:
            logger.warning("Token manager does not support auto-refresh")

    async def stop_auto_refresh(self) -> None:
        """Stop automatic token refresh task."""
        if self._refresh_task is not None:
            try:
                self._refresh_task.cancel()
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None
            logger.info("Auto-refresh stopped")

    @property
    def mode(self) -> str:
        """Get the current auth mode (broker, direct, raw, unknown)."""
        return self._mode

    @property
    def token_manager(self) -> Any:
        """Expose the underlying token manager for passing to CTraderSession."""
        return self._token_mgr
