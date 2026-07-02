"""Bootstrap signal experience tables from historical signal JSON."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .chains import build_chains
from .classifier import classify_messages
from .models import (
    ExperienceAuthor,
    ExperienceOverall,
    ExperiencePattern,
    ExperienceSession,
    SignalQualityLog,
)
from .store import SignalExperienceStore
from .updater import SignalExperienceUpdater

logger = logging.getLogger(__name__)


class MemorySignalExperienceStore:
    """In-memory store used to aggregate experience updates during bootstrap."""

    def __init__(self) -> None:
        self._authors: dict[str, ExperienceAuthor] = {}
        self._sessions: dict[int, ExperienceSession] = {}
        self._patterns: dict[str, ExperiencePattern] = {}
        self._overall: ExperienceOverall = ExperienceOverall()
        self._quality_logs: dict[int, SignalQualityLog] = {}

    def get_author(self, author: str) -> ExperienceAuthor | None:
        return self._authors.get(author)

    def save_author(self, author: ExperienceAuthor) -> None:
        self._authors[author.author] = author

    def list_authors(self) -> list[ExperienceAuthor]:
        return list(self._authors.values())

    def get_session(self, hour_utc: int) -> ExperienceSession | None:
        return self._sessions.get(hour_utc)

    def save_session(self, session: ExperienceSession) -> None:
        self._sessions[session.hour_utc] = session

    def list_sessions(self) -> list[ExperienceSession]:
        return list(self._sessions.values())

    def get_pattern(self, pattern_key: str) -> ExperiencePattern | None:
        return self._patterns.get(pattern_key)

    def save_pattern(self, pattern: ExperiencePattern) -> None:
        self._patterns[pattern.pattern_key] = pattern

    def list_patterns(self) -> list[ExperiencePattern]:
        return list(self._patterns.values())

    def get_overall(self, row_id: str = "global") -> ExperienceOverall | None:
        return self._overall

    def save_overall(self, overall: ExperienceOverall) -> None:
        self._overall = overall

    def get_quality_log(self, message_id: int) -> SignalQualityLog | None:
        return self._quality_logs.get(message_id)

    def save_quality_log(self, log: SignalQualityLog) -> None:
        self._quality_logs[log.message_id] = log

    def list_quality_logs_without_outcome(self, limit: int = 1000) -> list[SignalQualityLog]:
        return [log for log in self._quality_logs.values() if log.outcome is None]


def load_raw_signals(path: Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("messages", [])
    return data


def bootstrap(
    input_path: Path,
    store: SignalExperienceStore | None = None,
    reset: bool = False,
) -> dict[str, Any]:
    """Classify historical signals, build chains, and populate experience tables."""
    logging.basicConfig(level=logging.INFO)
    real_store = store or SignalExperienceStore()
    if reset:
        logger.info("Resetting experience tables before bootstrap")
        real_store.reset_all()

    messages = load_raw_signals(input_path)
    logger.info("Loaded %s raw messages", len(messages))

    classified = classify_messages(messages)
    logger.info("Classified %s messages", len(classified))

    chains = build_chains(classified)
    logger.info("Built %s lifecycle chains", len(chains))

    memory_store = MemorySignalExperienceStore()
    updater = SignalExperienceUpdater(memory_store)
    for i, chain in enumerate(chains):
        updater.update_from_chain(chain)
        if (i + 1) % 100 == 0:
            logger.info("Processed %s/%s chains", i + 1, len(chains))

    logger.info(
        "Flushing aggregated rows: authors=%s sessions=%s patterns=%s logs=%s",
        len(memory_store.list_authors()),
        len(memory_store.list_sessions()),
        len(memory_store.list_patterns()),
        len(memory_store._quality_logs),
    )
    for author in memory_store.list_authors():
        real_store.save_author(author)
    for session in memory_store.list_sessions():
        try:
            real_store.save_session(session)
        except Exception as exc:
            logger.error(
                "Failed to save session hour_utc=%s data=%s: %s",
                session.hour_utc,
                session.to_appwrite(),
                exc,
            )
            raise
    for pattern in memory_store.list_patterns():
        real_store.save_pattern(pattern)
    real_store.save_overall(memory_store.get_overall("global") or ExperienceOverall())
    for log in memory_store._quality_logs.values():
        real_store.save_quality_log(log)

    authors = real_store.list_authors()
    sessions = real_store.list_sessions()
    patterns = real_store.list_patterns()

    logger.info(
        "Bootstrap complete: authors=%s sessions=%s patterns=%s",
        len(authors),
        len(sessions),
        len(patterns),
    )

    return {
        "messages": len(messages),
        "classified": len(classified),
        "chains": len(chains),
        "authors": len(authors),
        "sessions": len(sessions),
        "patterns": len(patterns),
    }
