"""Appwrite TablesDB persistence for signal experience."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from appwrite.client import Client
from appwrite.exception import AppwriteException
from appwrite.id import ID
from appwrite.services.tables_db import TablesDB

from .models import (
    ExperienceAuthor,
    ExperienceOverall,
    ExperiencePattern,
    ExperienceSession,
    SignalQualityLog,
)

logger = logging.getLogger(__name__)

DEFAULT_DATABASE_ID = "market_data"


class SignalExperienceStore:
    """Sync store for signal experience rows."""

    def __init__(
        self,
        database_id: str | None = None,
        endpoint: str | None = None,
        project_id: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.database_id = database_id or os.getenv("APPWRITE_DATABASE_ID") or DEFAULT_DATABASE_ID
        self.endpoint = endpoint or os.getenv("APPWRITE_ENDPOINT", "https://sgp.cloud.appwrite.io/v1")
        self.project_id = project_id or os.getenv("APPWRITE_PROJECT_ID", "")
        self.api_key = api_key or os.getenv("APPWRITE_API_KEY", "")
        self._tables: TablesDB | None = None
        self._connect()

    def _connect(self) -> None:
        if not self.project_id or not self.api_key:
            raise RuntimeError("APPWRITE_PROJECT_ID and APPWRITE_API_KEY are required")
        client = (
            Client()
            .set_endpoint(self.endpoint)
            .set_project(self.project_id)
            .set_key(self.api_key)
        )
        self._tables = TablesDB(client)

    def _strip(self, row: Any) -> dict[str, Any]:
        """Return only the user-defined data fields from an Appwrite row."""
        if hasattr(row, "data"):
            data = row.data
            return dict(data) if data is not None else {}
        if hasattr(row, "to_dict"):
            d = row.to_dict()
        elif isinstance(row, dict):
            d = row
        else:
            d = dict(row)
        if isinstance(d.get("data"), dict):
            return dict(d["data"])
        return {k: v for k, v in d.items() if not k.startswith("$")}

    # ── Generic helpers ────────────────────────────────────────────────────────

    def _get(self, table_id: str, row_id: str) -> dict[str, Any] | None:
        if self._tables is None:
            return None
        try:
            result = self._tables.get_row(
                database_id=self.database_id,
                table_id=table_id,
                row_id=row_id,
            )
            return self._strip(result)
        except AppwriteException as e:
            if e.code == 404:
                return None
            raise

    def _create(self, table_id: str, body: dict[str, Any], row_id: str | None = None) -> dict[str, Any]:
        if self._tables is None:
            raise RuntimeError("Store not connected")
        effective_row_id = row_id or ID.unique()
        result = self._tables.create_row(
            database_id=self.database_id,
            table_id=table_id,
            row_id=effective_row_id,
            data=body,
        )
        return self._strip(result)

    def _update(self, table_id: str, row_id: str, body: dict[str, Any]) -> dict[str, Any]:
        if self._tables is None:
            raise RuntimeError("Store not connected")
        result = self._tables.update_row(
            database_id=self.database_id,
            table_id=table_id,
            row_id=row_id,
            data=body,
        )
        return self._strip(result)

    def _upsert(self, table_id: str, row_id: str, body: dict[str, Any]) -> dict[str, Any]:
        existing = self._get(table_id, row_id)
        if existing:
            return self._update(table_id, row_id, body)
        return self._create(table_id, body, row_id=row_id)

    def _list(self, table_id: str, queries: list | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        if self._tables is None:
            return []
        from appwrite.query import Query

        q = list(queries or [])
        q.append(Query.limit(limit))
        result = self._tables.list_rows(
            database_id=self.database_id,
            table_id=table_id,
            queries=q,
        )
        if hasattr(result, "rows"):
            return [self._strip(r) for r in result.rows]
        d = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        return [self._strip(r) for r in d.get("rows", [])]

    def _delete_all(self, table_id: str) -> None:
        """Delete all rows in a table. Used for idempotent reset."""
        if self._tables is None:
            return
        from appwrite.query import Query

        deleted = 0
        while True:
            result = self._tables.list_rows(
                database_id=self.database_id,
                table_id=table_id,
                queries=[Query.limit(100)],
            )
            if hasattr(result, "rows"):
                raw_rows = list(result.rows)
            else:
                d = result.to_dict() if hasattr(result, "to_dict") else dict(result)
                raw_rows = list(d.get("rows", []))
            if not raw_rows:
                break
            for raw in raw_rows:
                row_id: Any | None = None
                if hasattr(raw, "$id"):
                    row_id = getattr(raw, "$id")
                elif hasattr(raw, "id"):
                    row_id = raw.id
                elif hasattr(raw, "to_dict"):
                    row_id = raw.to_dict().get("$id")
                elif isinstance(raw, dict):
                    row_id = raw.get("$id") or raw.get("id")
                if row_id:
                    try:
                        self._tables.delete_row(
                            database_id=self.database_id,
                            table_id=table_id,
                            row_id=row_id,
                        )
                        deleted += 1
                    except AppwriteException as e:
                        logger.warning("Failed to delete row %s in %s: %s", row_id, table_id, e)
            if len(raw_rows) < 100:
                break
        logger.info("Deleted %s rows from %s", deleted, table_id)

    # ── Experience row accessors ───────────────────────────────────────────────

    def get_author(self, author: str) -> ExperienceAuthor | None:
        row = self._get("signal_experience_authors", f"author_{author}")
        if not row:
            return None
        return ExperienceAuthor(**row)

    def save_author(self, author: ExperienceAuthor) -> None:
        self._upsert("signal_experience_authors", f"author_{author.author}", author.to_appwrite())

    def list_authors(self) -> list[ExperienceAuthor]:
        rows = self._list("signal_experience_authors")
        return [ExperienceAuthor(**r) for r in rows]

    def get_session(self, hour_utc: int) -> ExperienceSession | None:
        row = self._get("signal_experience_sessions", f"session_{hour_utc}")
        if not row:
            return None
        return ExperienceSession(**row)

    def save_session(self, session: ExperienceSession) -> None:
        self._upsert("signal_experience_sessions", f"session_{session.hour_utc}", session.to_appwrite())

    def list_sessions(self) -> list[ExperienceSession]:
        rows = self._list("signal_experience_sessions")
        return [ExperienceSession(**r) for r in rows]

    def get_pattern(self, pattern_key: str) -> ExperiencePattern | None:
        row = self._get("signal_experience_patterns", f"pattern_{pattern_key}")
        if not row:
            return None
        return ExperiencePattern(**row)

    def save_pattern(self, pattern: ExperiencePattern) -> None:
        self._upsert("signal_experience_patterns", f"pattern_{pattern.pattern_key}", pattern.to_appwrite())

    def list_patterns(self) -> list[ExperiencePattern]:
        rows = self._list("signal_experience_patterns")
        return [ExperiencePattern(**r) for r in rows]

    def get_overall(self, row_id: str = "global") -> ExperienceOverall | None:
        row = self._get("signal_experience_overall", row_id)
        if not row:
            return None
        return ExperienceOverall(**row)

    def save_overall(self, overall: ExperienceOverall, row_id: str = "global") -> None:
        self._upsert("signal_experience_overall", row_id, overall.to_appwrite())

    # ── Quality log accessors ──────────────────────────────────────────────────

    def get_quality_log(self, message_id: int) -> SignalQualityLog | None:
        row = self._get("signal_quality_log", f"log_{message_id}")
        if not row:
            return None
        return SignalQualityLog(**row)

    def save_quality_log(self, log: SignalQualityLog) -> None:
        self._upsert("signal_quality_log", f"log_{log.message_id}", log.to_appwrite())

    def list_quality_logs_without_outcome(self, limit: int = 1000) -> list[SignalQualityLog]:
        from appwrite.query import Query

        # Appwrite query for outcome is null is not directly supported; list and filter.
        rows = self._list("signal_quality_log", queries=[Query.limit(limit)], limit=limit)
        logs = [SignalQualityLog(**r) for r in rows]
        return [log for log in logs if log.outcome is None]

    # ── Reset ──────────────────────────────────────────────────────────────────

    def reset_all(self) -> None:
        """Clear all experience tables. Use with caution (intended for re-bootstrap)."""
        for table_id in (
            "signal_experience_authors",
            "signal_experience_sessions",
            "signal_experience_patterns",
            "signal_experience_overall",
            "signal_quality_log",
        ):
            logger.info("Resetting table %s", table_id)
            self._delete_all(table_id)
