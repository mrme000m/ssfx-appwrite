"""Appwrite TablesDB account discovery for active cTrader slave accounts.

Polls the ``slave_accounts`` table for rows with ``status=active`` and joins the
``accounts`` table by ``grant_id`` to obtain each account's ``isLive`` flag and
``ctidTraderAccountId``.  The discovery result is a set of ``AccountRef`` objects
partitioned by environment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from appwrite.client import Client
from appwrite.query import Query
from appwrite.services.tables_db import TablesDB

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AccountRef:
    """Reference to a single cTrader account discovered in Appwrite."""

    grant_id: str
    ctid_trader_account_id: int
    is_live: bool
    username: str | None = None
    appwrite_user_id: str | None = None
    broker_name: str | None = None
    selected: bool = False


class AccountDiscovery:
    """Polls Appwrite TablesDB and emits added/removed account diffs."""

    def __init__(
        self,
        appwrite_client: Client,
        database_id: str,
        slave_accounts_table: str = "slave_accounts",
        accounts_table: str = "accounts",
    ):
        self._client = appwrite_client
        self._tables = TablesDB(appwrite_client)
        self._database_id = database_id
        self._slave_accounts_table = slave_accounts_table
        self._accounts_table = accounts_table
        self._last_refs: set[AccountRef] = set()

    @property
    def last_refs(self) -> set[AccountRef]:
        return set(self._last_refs)

    def discover(self) -> set[AccountRef]:
        """Return the current set of active cTrader accounts."""
        slaves = self._list_active_slaves()
        if not slaves:
            return set()

        refs: set[AccountRef] = set()
        for slave in slaves:
            grant_id = slave.get("grant_id", "")
            if not grant_id:
                continue
            username = slave.get("username") or None
            appwrite_user_id = slave.get("appwrite_user_id") or None

            accounts = self._list_accounts_for_grant(grant_id)
            if accounts:
                for acc in accounts:
                    ctid_raw = acc.get("ctidTraderAccountId")
                    if ctid_raw is None:
                        continue
                    try:
                        ctid = int(ctid_raw)
                    except (ValueError, TypeError):
                        logger.warning(
                            "Invalid ctidTraderAccountId %r for grant %s", ctid_raw, grant_id
                        )
                        continue
                    is_live = bool(acc.get("isLive", False))
                    refs.add(
                        AccountRef(
                            grant_id=grant_id,
                            ctid_trader_account_id=ctid,
                            is_live=is_live,
                            username=username,
                            appwrite_user_id=appwrite_user_id,
                            broker_name=acc.get("brokerName") or acc.get("brokerTitleShort") or acc.get("brokerTitle") or None,
                            selected=bool(acc.get("selected", False)),
                        )
                    )
            else:
                # Fallback: if the accounts table has not been populated yet,
                # try the legacy ctrader_account_ids list on the slave row.
                refs.update(self._refs_from_legacy_ids(slave, username, appwrite_user_id))

        return refs

    def diff(self, current: set[AccountRef] | None = None) -> tuple[set[AccountRef], set[AccountRef]]:
        """Compare ``current`` to the last known set and return (added, removed)."""
        if current is None:
            current = self.discover()
        added = current - self._last_refs
        removed = self._last_refs - current
        self._last_refs = current
        return added, removed

    def _list_active_slaves(self) -> list[dict[str, Any]]:
        try:
            result = self._tables.list_rows(
                database_id=self._database_id,
                table_id=self._slave_accounts_table,
                queries=[Query.equal("status", "active")],
            )
        except Exception as exc:
            logger.error("Failed to list active slave_accounts: %s", exc)
            return []
        return [self._row_data(r) for r in self._rows_from_result(result)]

    def _list_accounts_for_grant(self, grant_id: str) -> list[dict[str, Any]]:
        try:
            result = self._tables.list_rows(
                database_id=self._database_id,
                table_id=self._accounts_table,
                queries=[Query.equal("grant_id", grant_id)],
            )
        except Exception as exc:
            logger.warning("Failed to list accounts for grant %s: %s", grant_id, exc)
            return []
        return [self._row_data(r) for r in self._rows_from_result(result)]

    def _refs_from_legacy_ids(
        self,
        slave: dict[str, Any],
        username: str | None,
        appwrite_user_id: str | None,
    ) -> set[AccountRef]:
        refs: set[AccountRef] = set()
        raw = slave.get("ctrader_account_ids", "")
        grant_id = slave.get("grant_id", "")
        if not raw or not grant_id:
            return refs
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                ctid = int(part)
            except ValueError:
                continue
            # Without the accounts table we don't know isLive; assume both.
            refs.add(
                AccountRef(
                    grant_id=grant_id,
                    ctid_trader_account_id=ctid,
                    is_live=False,
                    username=username,
                    appwrite_user_id=appwrite_user_id,
                    selected=False,
                )
            )
            refs.add(
                AccountRef(
                    grant_id=grant_id,
                    ctid_trader_account_id=ctid,
                    is_live=True,
                    username=username,
                    appwrite_user_id=appwrite_user_id,
                    selected=False,
                )
            )
        return refs

    @staticmethod
    def _rows_from_result(result: Any) -> list[Any]:
        if hasattr(result, "rows"):
            return list(result.rows)
        d = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        return list(d.get("rows", []))

    @staticmethod
    def _row_data(row: Any) -> dict[str, Any]:
        if hasattr(row, "data"):
            data = row.data
            return dict(data) if data is not None else {}
        if hasattr(row, "to_dict"):
            d = row.to_dict()
            if isinstance(d.get("data"), dict):
                return dict(d["data"])
            return {k: v for k, v in d.items() if not k.startswith("$")}
        if isinstance(row, dict):
            if isinstance(row.get("data"), dict):
                return dict(row["data"])
            return {k: v for k, v in row.items() if not k.startswith("$")}
        return dict(row)
