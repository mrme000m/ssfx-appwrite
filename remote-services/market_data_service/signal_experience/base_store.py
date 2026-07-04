"""Base interface for signal experience stores."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import (
    ExperienceAuthor,
    ExperienceOverall,
    ExperiencePattern,
    ExperienceSession,
    SignalQualityLog,
)


class BaseSignalExperienceStore(ABC):
    """Abstract base class for signal experience stores."""

    @abstractmethod
    def get_author(self, author: str) -> ExperienceAuthor | None:
        pass

    @abstractmethod
    def save_author(self, author: ExperienceAuthor) -> None:
        pass

    @abstractmethod
    def list_authors(self) -> list[ExperienceAuthor]:
        pass

    @abstractmethod
    def get_session(self, hour_utc: int) -> ExperienceSession | None:
        pass

    @abstractmethod
    def save_session(self, session: ExperienceSession) -> None:
        pass

    @abstractmethod
    def list_sessions(self) -> list[ExperienceSession]:
        pass

    @abstractmethod
    def get_pattern(self, pattern_key: str) -> ExperiencePattern | None:
        pass

    @abstractmethod
    def save_pattern(self, pattern: ExperiencePattern) -> None:
        pass

    @abstractmethod
    def list_patterns(self) -> list[ExperiencePattern]:
        pass

    @abstractmethod
    def get_overall(self, row_id: str = "global") -> ExperienceOverall | None:
        pass

    @abstractmethod
    def save_overall(self, overall: ExperienceOverall, row_id: str = "global") -> None:
        pass

    @abstractmethod
    def get_quality_log(self, message_id: int) -> SignalQualityLog | None:
        pass

    @abstractmethod
    def save_quality_log(self, log: SignalQualityLog) -> None:
        pass

    @abstractmethod
    def list_quality_logs_without_outcome(self, limit: int = 1000) -> list[SignalQualityLog]:
        pass

    @abstractmethod
    def list_recent_logs_by_author(
        self,
        author: str,
        limit: int = 10,
        exclude_message_id: int | None = None,
    ) -> list[SignalQualityLog]:
        pass

    @abstractmethod
    def reset_all(self) -> None:
        pass