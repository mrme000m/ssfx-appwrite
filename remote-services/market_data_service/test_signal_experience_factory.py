"""Test the signal experience factory and stores."""
import os
import tempfile
import unittest
from pathlib import Path

from .signal_experience.factory import create_signal_experience_store
from .signal_experience.models import (
    ExperienceAuthor,
    ExperienceOverall,
    ExperiencePattern,
    ExperienceSession,
    SignalQualityLog,
)


class TestSignalExperienceFactory(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for SQLite tests
        self.temp_dir = tempfile.mkdtemp()
        self.sqlite_path = Path(self.temp_dir) / "test_experience.db"
        
    def tearDown(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
    
    def test_sqlite_store_creation(self):
        """Test creating a SQLite store."""
        store = create_signal_experience_store(
            backend="sqlite",
            db_path=str(self.sqlite_path),
        )
        
        # Test basic operations
        author = ExperienceAuthor(author="test_author", total_signals=10, win_rate=0.8)
        store.save_author(author)
        
        retrieved = store.get_author("test_author")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.author, "test_author")
        self.assertEqual(retrieved.total_signals, 10)
        self.assertEqual(retrieved.win_rate, 0.8)
    
    def test_sqlite_quality_log(self):
        """Test quality log operations."""
        store = create_signal_experience_store(
            backend="sqlite",
            db_path=str(self.sqlite_path),
        )
        
        log = SignalQualityLog(
            message_id=123,
            chat_id="-1001661400724",
            author="test_trader",
            raw_text="BUY XAUUSD @ 2000",
            quality_score=0.9,
            decision="ENTER",
            outcome=None,  # No outcome yet
        )
        store.save_quality_log(log)
        
        retrieved = store.get_quality_log(123)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.message_id, 123)
        self.assertEqual(retrieved.author, "test_trader")
        
        # Test listing logs without outcome
        logs_without_outcome = store.list_quality_logs_without_outcome()
        self.assertEqual(len(logs_without_outcome), 1)
        
        # Test listing recent logs by author
        recent_logs = store.list_recent_logs_by_author("test_trader", limit=5)
        self.assertEqual(len(recent_logs), 1)
    
    def test_sqlite_pattern_and_session(self):
        """Test pattern and session operations."""
        store = create_signal_experience_store(
            backend="sqlite",
            db_path=str(self.sqlite_path),
        )
        
        # Test pattern
        pattern = ExperiencePattern(
            pattern_key="XAUUSD_BUY_MARKET",
            symbol="XAUUSD",
            direction="BUY",
            order_type="MARKET",
            total_signals=50,
            win_rate=0.75,
        )
        store.save_pattern(pattern)
        
        retrieved_pattern = store.get_pattern("XAUUSD_BUY_MARKET")
        self.assertIsNotNone(retrieved_pattern)
        self.assertEqual(retrieved_pattern.symbol, "XAUUSD")
        
        # Test session
        session = ExperienceSession(
            hour_utc=10,
            total_signals=100,
            win_rate=0.65,
        )
        store.save_session(session)
        
        retrieved_session = store.get_session(10)
        self.assertIsNotNone(retrieved_session)
        self.assertEqual(retrieved_session.hour_utc, 10)
        
        # Test overall
        overall = ExperienceOverall(
            rolling_30d_win_rate=0.68,
            signals_today=5,
            good_vs_bad_ratio=1.5,
        )
        store.save_overall(overall)
        
        retrieved_overall = store.get_overall("global")
        self.assertIsNotNone(retrieved_overall)
        self.assertEqual(retrieved_overall.signals_today, 5)
    
    def test_reset_all(self):
        """Test reset_all operation."""
        store = create_signal_experience_store(
            backend="sqlite",
            db_path=str(self.sqlite_path),
        )
        
        # Add some data
        author = ExperienceAuthor(author="test", total_signals=1)
        store.save_author(author)
        
        log = SignalQualityLog(message_id=1, chat_id="test", author="test", raw_text="test")
        store.save_quality_log(log)
        
        # Reset
        store.reset_all()
        
        # Verify data is gone
        self.assertIsNone(store.get_author("test"))
        self.assertIsNone(store.get_quality_log(1))


if __name__ == "__main__":
    unittest.main()