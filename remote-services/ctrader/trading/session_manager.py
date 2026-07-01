"""Pool and manage CTraderSession instances per (grant_id, ctid)."""
from __future__ import annotations

import logging

from ctrader_client import CTraderSession
from ctrader_client.session import TransportType

from .auth import TokenClient

logger = logging.getLogger(__name__)


class SessionManager:
    """Creates and caches CTraderSession objects."""

    def __init__(
        self,
        token_client: TokenClient,
        broker_url: str,
        transport: TransportType = TransportType.TCP,
        client_id: str = "",
        client_secret: str = "",
    ):
        self._token_client = token_client
        self._broker_url = broker_url
        self._transport = transport
        self._client_id = client_id
        self._client_secret = client_secret
        self._sessions: dict[tuple[str, int], CTraderSession] = {}

    async def get_session(self, grant_id: str, ctid: int, use_live: bool = False) -> CTraderSession:
        key = (grant_id, ctid)
        session = self._sessions.get(key)
        if session and session._transport.is_connected:
            return session

        access_token = await self._token_client.refresh(grant_id)
        session = CTraderSession(
            client_id=self._client_id,
            client_secret=self._client_secret,
            access_token=access_token,
            refresh_token="",
            account_id=ctid,
            use_live=use_live,
            transport_type=self._transport,
            broker_url=self._broker_url,
            grant_id=grant_id,
            internal_api_key=self._token_client._internal_api_key,
        )
        await session.start()
        self._sessions[key] = session
        logger.info("Started CTraderSession for %s:%s", grant_id, ctid)
        return session

    async def close_all(self) -> None:
        for (grant_id, ctid), session in list(self._sessions.items()):
            try:
                await session.stop()
                logger.info("Closed session %s:%s", grant_id, ctid)
            except Exception as exc:
                logger.warning("Error closing session %s:%s: %s", grant_id, ctid, exc)
        self._sessions.clear()

    def remove(self, grant_id: str, ctid: int) -> None:
        self._sessions.pop((grant_id, ctid), None)
