"""Perplexity Space abstraction for the gold market knowledge base.

A Space stores the accumulating long-term picture (markdown report files) and
provides a queryable context for follow-up research questions. This module
wraps the vendored ``pplx`` client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .config import PplxAgentSettings, get_settings
from .pplx_client import PplxClient, PplxClientError


class SpaceManager:
    """Manage the Gold Market Intelligence Perplexity Space."""

    def __init__(self, settings: PplxAgentSettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = PplxClient(settings=self._settings)

    # ------------------------------------------------------------------
    # Space lifecycle
    # ------------------------------------------------------------------

    def ensure_space(self) -> str:
        """Return existing space UUID or create a new one."""
        existing_uuid = self._settings.gold_market_space_uuid
        if existing_uuid:
            return existing_uuid

        instructions = (
            "You are a senior gold market analyst. Always ground answers in the uploaded "
            "daily reports, and be explicit about timeframes (short-term vs medium-term "
            "vs long-term). Cite sources and key levels whenever possible."
        )
        try:
            result = self._client.raw.create_space(
                title=self._settings.gold_market_space_name,
                description="Long-term knowledge base for XAUUSD macro, technical and fundamental analysis.",
                emoji="1f4c8",
                instructions=instructions,
                access=1,
                enable_web=True,
            )
        except Exception as exc:
            raise PplxClientError(f"Failed to create Perplexity Space: {exc}") from exc

        uuid = _extract_uuid(result)
        if not uuid:
            raise PplxClientError(f"Space creation response missing uuid: {result}")

        # Persist back to caller via environment note (caller must persist to Appwrite).
        self._settings.gold_market_space_uuid = uuid
        return uuid

    def list_spaces(self) -> list[dict[str, Any]]:
        """List all user spaces."""
        raw = self._client.raw.list_spaces()
        rows = raw if isinstance(raw, list) else raw.get("collections", raw.get("spaces", []))
        return rows if isinstance(rows, list) else []

    def find_space_by_name(self, name: str) -> dict[str, Any] | None:
        """Find a space by exact name match."""
        for space in self.list_spaces():
            title = space.get("title", "") if isinstance(space, dict) else getattr(space, "title", "")
            if title == name:
                return space if isinstance(space, dict) else space.__dict__
        return None

    # ------------------------------------------------------------------
    # Reports / files
    # ------------------------------------------------------------------

    def upload_report(self, space_uuid: str, report_text: str, filename: str | None = None) -> dict[str, Any]:
        """Upload a markdown report file into the Space."""
        if not filename:
            date = datetime.now(UTC).strftime("%Y-%m-%d")
            filename = f"gold_market_report_{date}.md"

        try:
            return self._client.raw.upload_file_to_space(
                uuid=space_uuid,
                filename=filename,
                file_content=report_text.encode("utf-8"),
                content_type="text/markdown",
            )
        except Exception as exc:
            raise PplxClientError(f"Failed to upload report to Space: {exc}") from exc

    def list_reports(self, space_uuid: str, limit: int = 50) -> list[dict[str, Any]]:
        """List report files in the Space."""
        raw = self._client.raw.list_space_files(uuid=space_uuid, page_size=limit)
        if isinstance(raw, dict):
            return raw.get("files", raw.get("documents", []))
        return []

    def delete_old_reports(self, space_uuid: str, keep: int = 90) -> list[str]:
        """Remove report files older than ``keep`` days. Returns deleted UUIDs."""
        files = self.list_reports(space_uuid, limit=500)
        cutoff = datetime.now(UTC).timestamp() - keep * 86400
        deleted: list[str] = []
        for f in files:
            if not isinstance(f, dict):
                continue
            name = f.get("filename", "")
            updated = f.get("updated_at") or f.get("created_at")
            if not name.startswith("gold_market_report_") or not updated:
                continue
            try:
                ts = _parse_iso(updated)
                if ts.timestamp() < cutoff:
                    file_uuid = f.get("uuid") or f.get("id")
                    if file_uuid:
                        self._client.raw.delete_space_files(space_uuid, [file_uuid])
                        deleted.append(file_uuid)
            except Exception:
                continue
        return deleted

    # ------------------------------------------------------------------
    # Long-term picture thread
    # ------------------------------------------------------------------

    def update_long_term_thread(
        self,
        space_uuid: str,
        long_term_summary: str,
    ) -> dict[str, Any]:
        """Start or append to the canonical long-term-picture thread in the Space.

        The thread is identified by title prefix ``LONGTERM:``. Perplexity threads
        inside a Space can keep context if we pass a backend_uuid, but the current
        web client returns a single event per query; we therefore create/update a
        pinned markdown asset and post a follow-up question referencing it.
        """
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

        # Store the synthesized summary as a pinned file inside the Space.
        summary_md = f"# Long-Term Picture — {now}\n\n{long_term_summary}"
        self.upload_report(space_uuid, summary_md, filename="LONGTERM_pinned_summary.md")

        # Query the space to synthesize a one-paragraph executive summary.
        query = (
            "Based on the uploaded LONGTERM_pinned_summary.md and all daily reports, "
            "produce a one-paragraph executive summary of the current long-term gold outlook. "
            "Include trend, key levels, and main risks."
        )
        return self._client.search_space(space_uuid, query, mode="pro")

    def query(self, space_uuid: str, question: str, mode: str = "pro") -> dict[str, Any]:
        """Ask a question against the Space knowledge base."""
        return self._client.search_space(space_uuid, question, mode=mode)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_uuid(result: Any) -> str | None:
    if isinstance(result, dict):
        return result.get("uuid") or result.get("id") or result.get("collection_uuid")
    return getattr(result, "uuid", None) or getattr(result, "id", None)


def _parse_iso(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def format_citations(citations: list[dict[str, Any]]) -> str:
    if not citations:
        return ""
    lines = ["\n### Sources"]
    for i, c in enumerate(citations, 1):
        title = c.get("title") or c.get("name") or "Source"
        url = c.get("url") or ""
        lines.append(f"{i}. [{title}]({url})")
    return "\n".join(lines)
