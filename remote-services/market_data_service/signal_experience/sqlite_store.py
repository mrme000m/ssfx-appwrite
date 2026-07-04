"""SQLite persistence for signal experience."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .base_store import BaseSignalExperienceStore
from .models import (
    ExperienceAuthor,
    ExperienceOverall,
    ExperiencePattern,
    ExperienceSession,
    SignalQualityLog,
)

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


class SQLiteSignalExperienceStore(BaseSignalExperienceStore):
    """SQLite store for signal experience rows."""

    def __init__(self, db_path: str = "/app/data/experience.db") -> None:
        self.db_path = db_path
        self._ensure_schema()
        logger.info("SQLiteSignalExperienceStore initialized at %s", db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        import os

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._connect() as conn:
            # Authors table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_experience_authors (
                    author TEXT PRIMARY KEY,
                    total_signals INTEGER DEFAULT 0,
                    win_count INTEGER DEFAULT 0,
                    loss_count INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0.0,
                    avg_profit_pips REAL,
                    avg_loss_pips REAL,
                    profit_factor REAL,
                    expectancy REAL,
                    current_streak INTEGER DEFAULT 0,
                    max_drawdown_signals INTEGER DEFAULT 0,
                    avg_rr REAL,
                    last_signal_at TEXT,
                    updated_at TEXT
                )
            """)
            
            # Sessions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_experience_sessions (
                    hour_utc INTEGER PRIMARY KEY,
                    total_signals INTEGER DEFAULT 0,
                    win_count INTEGER DEFAULT 0,
                    loss_count INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0.0,
                    avg_rr REAL,
                    avg_time_to_update_min REAL,
                    noise_ratio REAL,
                    updated_at TEXT
                )
            """)
            
            # Patterns table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_experience_patterns (
                    pattern_key TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    order_type TEXT,
                    total_signals INTEGER DEFAULT 0,
                    win_count INTEGER DEFAULT 0,
                    loss_count INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0.0,
                    avg_sl_pips REAL,
                    avg_tp_pips REAL,
                    expectancy REAL,
                    confidence_score REAL,
                    updated_at TEXT
                )
            """)
            
            # Overall table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_experience_overall (
                    row_id TEXT PRIMARY KEY,
                    rolling_30d_win_rate REAL,
                    signals_today INTEGER DEFAULT 0,
                    good_vs_bad_ratio REAL,
                    insights_json TEXT,
                    updated_at TEXT
                )
            """)
            
            # Quality log table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_quality_log (
                    message_id INTEGER PRIMARY KEY,
                    chat_id TEXT,
                    author TEXT,
                    raw_text TEXT,
                    quality_score REAL,
                    factors_json TEXT,
                    decision TEXT,
                    outcome TEXT,
                    outcome_pips REAL,
                    closed_at TEXT,
                    updated_at TEXT
                )
            """)
            
            # Create indices
            conn.execute("CREATE INDEX IF NOT EXISTS idx_quality_log_author ON signal_quality_log(author)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_quality_log_outcome ON signal_quality_log(outcome)")

            conn.commit()

    # ── Authors ────────────────────────────────────────────────────────────────

    def get_author(self, author: str) -> ExperienceAuthor | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM signal_experience_authors WHERE author = ?",
                (author,),
            ).fetchone()
        return self._row_to_author(row) if row else None

    def save_author(self, author: ExperienceAuthor) -> None:
        data = author.to_appwrite()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO signal_experience_authors
                (author, total_signals, win_count, loss_count, win_rate, avg_profit_pips,
                 avg_loss_pips, profit_factor, expectancy, current_streak, max_drawdown_signals,
                 avg_rr, last_signal_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["author"],
                    data["total_signals"],
                    data["win_count"],
                    data["loss_count"],
                    data["win_rate"],
                    data["avg_profit_pips"],
                    data["avg_loss_pips"],
                    data["profit_factor"],
                    data["expectancy"],
                    data["current_streak"],
                    data["max_drawdown_signals"],
                    data["avg_rr"],
                    data["last_signal_at"],
                    data["updated_at"],
                ),
            )
            conn.commit()

    def list_authors(self) -> list[ExperienceAuthor]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM signal_experience_authors ORDER BY author"
            ).fetchall()
        return [self._row_to_author(row) for row in rows]

    # ── Sessions ───────────────────────────────────────────────────────────────

    def get_session(self, hour_utc: int) -> ExperienceSession | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM signal_experience_sessions WHERE hour_utc = ?",
                (hour_utc,),
            ).fetchone()
        return self._row_to_session(row) if row else None

    def save_session(self, session: ExperienceSession) -> None:
        data = session.to_appwrite()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO signal_experience_sessions
                (hour_utc, total_signals, win_count, loss_count, win_rate,
                 avg_rr, avg_time_to_update_min, noise_ratio, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["hour_utc"],
                    data["total_signals"],
                    data["win_count"],
                    data["loss_count"],
                    data["win_rate"],
                    data["avg_rr"],
                    data["avg_time_to_update_min"],
                    data["noise_ratio"],
                    data["updated_at"],
                ),
            )
            conn.commit()

    def list_sessions(self) -> list[ExperienceSession]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM signal_experience_sessions ORDER BY hour_utc"
            ).fetchall()
        return [self._row_to_session(row) for row in rows]

    # ── Patterns ───────────────────────────────────────────────────────────────

    def get_pattern(self, pattern_key: str) -> ExperiencePattern | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM signal_experience_patterns WHERE pattern_key = ?",
                (pattern_key,),
            ).fetchone()
        return self._row_to_pattern(row) if row else None

    def save_pattern(self, pattern: ExperiencePattern) -> None:
        data = pattern.to_appwrite()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO signal_experience_patterns
                (pattern_key, symbol, direction, order_type, total_signals, win_count,
                 loss_count, win_rate, avg_sl_pips, avg_tp_pips, expectancy,
                 confidence_score, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["pattern_key"],
                    data["symbol"],
                    data["direction"],
                    data["order_type"],
                    data["total_signals"],
                    data["win_count"],
                    data["loss_count"],
                    data["win_rate"],
                    data["avg_sl_pips"],
                    data["avg_tp_pips"],
                    data["expectancy"],
                    data["confidence_score"],
                    data["updated_at"],
                ),
            )
            conn.commit()

    def list_patterns(self) -> list[ExperiencePattern]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM signal_experience_patterns ORDER BY pattern_key"
            ).fetchall()
        return [self._row_to_pattern(row) for row in rows]

    # ── Overall ────────────────────────────────────────────────────────────────

    def get_overall(self, row_id: str = "global") -> ExperienceOverall | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM signal_experience_overall WHERE row_id = ?",
                (row_id,),
            ).fetchone()
        return self._row_to_overall(row) if row else None

    def save_overall(self, overall: ExperienceOverall, row_id: str = "global") -> None:
        data = overall.to_appwrite()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO signal_experience_overall
                (row_id, rolling_30d_win_rate, signals_today, good_vs_bad_ratio,
                 insights_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    data["rolling_30d_win_rate"],
                    data["signals_today"],
                    data["good_vs_bad_ratio"],
                    data["insights_json"],
                    data["updated_at"],
                ),
            )
            conn.commit()

    # ── Quality Log ────────────────────────────────────────────────────────────

    def get_quality_log(self, message_id: int) -> SignalQualityLog | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM signal_quality_log WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        return self._row_to_quality_log(row) if row else None

    def save_quality_log(self, log: SignalQualityLog) -> None:
        data = log.to_appwrite()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO signal_quality_log
                (message_id, chat_id, author, raw_text, quality_score, factors_json,
                 decision, outcome, outcome_pips, closed_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["message_id"],
                    data["chat_id"],
                    data["author"],
                    data["raw_text"],
                    data["quality_score"],
                    data["factors_json"],
                    data["decision"],
                    data["outcome"],
                    data["outcome_pips"],
                    data["closed_at"],
                    data["updated_at"],
                ),
            )
            conn.commit()

    def list_quality_logs_without_outcome(self, limit: int = 1000) -> list[SignalQualityLog]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM signal_quality_log
                WHERE outcome IS NULL
                ORDER BY message_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_quality_log(row) for row in rows]

    def list_recent_logs_by_author(
        self,
        author: str,
        limit: int = 10,
        exclude_message_id: int | None = None,
    ) -> list[SignalQualityLog]:
        query = """
            SELECT * FROM signal_quality_log
            WHERE author = ?
            ORDER BY message_id DESC
            LIMIT ?
        """
        params = [author, limit * 2]  # Fetch extra to filter out excluded
        
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        
        logs = [self._row_to_quality_log(row) for row in rows]
        if exclude_message_id is not None:
            logs = [log for log in logs if log.message_id != exclude_message_id]
        return logs[:limit]

    # ── Reset ──────────────────────────────────────────────────────────────────

    def reset_all(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM signal_experience_authors")
            conn.execute("DELETE FROM signal_experience_sessions")
            conn.execute("DELETE FROM signal_experience_patterns")
            conn.execute("DELETE FROM signal_experience_overall")
            conn.execute("DELETE FROM signal_quality_log")
            conn.commit()
        logger.info("Reset all SQLite signal experience tables")

    # ── Serialization helpers ─────────────────────────────────────────────────

    @staticmethod
    def _row_to_author(row: sqlite3.Row) -> ExperienceAuthor:
        return ExperienceAuthor(
            author=row["author"],
            total_signals=row["total_signals"],
            win_count=row["win_count"],
            loss_count=row["loss_count"],
            win_rate=row["win_rate"],
            avg_profit_pips=row["avg_profit_pips"],
            avg_loss_pips=row["avg_loss_pips"],
            profit_factor=row["profit_factor"],
            expectancy=row["expectancy"],
            current_streak=row["current_streak"],
            max_drawdown_signals=row["max_drawdown_signals"],
            avg_rr=row["avg_rr"],
            last_signal_at=row["last_signal_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> ExperienceSession:
        return ExperienceSession(
            hour_utc=row["hour_utc"],
            total_signals=row["total_signals"],
            win_count=row["win_count"],
            loss_count=row["loss_count"],
            win_rate=row["win_rate"],
            avg_rr=row["avg_rr"],
            avg_time_to_update_min=row["avg_time_to_update_min"],
            noise_ratio=row["noise_ratio"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_pattern(row: sqlite3.Row) -> ExperiencePattern:
        return ExperiencePattern(
            pattern_key=row["pattern_key"],
            symbol=row["symbol"],
            direction=row["direction"],
            order_type=row["order_type"],
            total_signals=row["total_signals"],
            win_count=row["win_count"],
            loss_count=row["loss_count"],
            win_rate=row["win_rate"],
            avg_sl_pips=row["avg_sl_pips"],
            avg_tp_pips=row["avg_tp_pips"],
            expectancy=row["expectancy"],
            confidence_score=row["confidence_score"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_overall(row: sqlite3.Row) -> ExperienceOverall:
        insights_json = row["insights_json"]
        insights = json.loads(insights_json) if insights_json else None
        return ExperienceOverall(
            rolling_30d_win_rate=row["rolling_30d_win_rate"],
            signals_today=row["signals_today"],
            good_vs_bad_ratio=row["good_vs_bad_ratio"],
            insights_json=insights,
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_quality_log(row: sqlite3.Row) -> SignalQualityLog:
        return SignalQualityLog(
            message_id=row["message_id"],
            chat_id=row["chat_id"],
            author=row["author"],
            raw_text=row["raw_text"],
            quality_score=row["quality_score"],
            factors_json=row["factors_json"],
            decision=row["decision"],
            outcome=row["outcome"],
            outcome_pips=row["outcome_pips"],
            closed_at=row["closed_at"],
            updated_at=row["updated_at"],
        )