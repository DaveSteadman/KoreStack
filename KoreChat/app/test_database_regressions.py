import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database as db


class DatabaseRegressionTests(unittest.TestCase):
    def test_conversation_get_preserves_malformed_scratchpad_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_db_dir = Path(tmp)
            original_db_path = db._DB_PATH
            original_wal_initialized = db._wal_initialized
            db._DB_PATH = None
            db._wal_initialized = False

            try:
                with patch.dict(db.cfg, {"data_dir": str(temp_db_dir)}):
                    db.init_db()
                    conversation = db.conversation_create("webchat", subject="Test")
                    with db._conn() as connection:
                        connection.execute(
                            "UPDATE conversations SET scratchpad = ? WHERE id = ?",
                            ('{"broken": ', conversation["id"]),
                        )

                    loaded = db.conversation_get(conversation["id"])
            finally:
                db._DB_PATH = original_db_path
                db._wal_initialized = original_wal_initialized

        self.assertEqual(loaded["scratchpad"], {})
        self.assertEqual(loaded["scratchpad_raw"], '{"broken": ')
        self.assertIn("scratchpad JSON decode failed", loaded["scratchpad_parse_error"])

    def test_conversation_get_preserves_malformed_datasets_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_db_dir = Path(tmp)
            original_db_path = db._DB_PATH
            original_wal_initialized = db._wal_initialized
            db._DB_PATH = None
            db._wal_initialized = False

            try:
                with patch.dict(db.cfg, {"data_dir": str(temp_db_dir)}):
                    db.init_db()
                    conversation = db.conversation_create("webchat", subject="Test")
                    with db._conn() as connection:
                        connection.execute(
                            "UPDATE conversations SET datasets = ? WHERE id = ?",
                            ('{"broken": ', conversation["id"]),
                        )

                    loaded = db.conversation_get(conversation["id"])
            finally:
                db._DB_PATH = original_db_path
                db._wal_initialized = original_wal_initialized

        self.assertEqual(loaded["datasets"], {})
        self.assertEqual(loaded["datasets_raw"], '{"broken": ')
        self.assertIn("datasets JSON decode failed", loaded["datasets_parse_error"])

    def test_init_db_migrates_legacy_datasets_out_of_scratchpad(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_db_dir = Path(tmp)
            original_db_path = db._DB_PATH
            original_wal_initialized = db._wal_initialized
            db._DB_PATH = None
            db._wal_initialized = False

            try:
                with patch.dict(db.cfg, {"data_dir": str(temp_db_dir)}):
                    connection = sqlite3.connect(temp_db_dir / "korechat.db")
                    try:
                        connection.execute(
                            """
                            CREATE TABLE conversations (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                channel_type TEXT NOT NULL DEFAULT 'webchat',
                                profile TEXT NOT NULL DEFAULT 'admin',
                                status TEXT NOT NULL DEFAULT 'active',
                                subject TEXT,
                                protected INTEGER NOT NULL DEFAULT 0,
                                external_id TEXT,
                                thread_summary TEXT NOT NULL DEFAULT '',
                                scratchpad TEXT NOT NULL DEFAULT '{}',
                                input_history TEXT NOT NULL DEFAULT '[]',
                                background_context TEXT NOT NULL DEFAULT '',
                                token_estimate INTEGER NOT NULL DEFAULT 0,
                                turn_count INTEGER NOT NULL DEFAULT 0,
                                last_activity_at TEXT NOT NULL,
                                created_at TEXT NOT NULL,
                                updated_at TEXT NOT NULL
                            )
                            """
                        )
                        connection.execute(
                            "INSERT INTO conversations (channel_type, profile, status, subject, thread_summary, scratchpad, input_history, background_context, token_estimate, turn_count, last_activity_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                "webchat",
                                "admin",
                                "active",
                                "Legacy",
                                "",
                                '{"topic": "alpha", "__datasets": {"feed_items_raw": {"dataset_id": "ds_1", "count": 2}}}',
                                "[]",
                                "",
                                0,
                                0,
                                "2026-05-30T00:00:00+00:00",
                                "2026-05-30T00:00:00+00:00",
                                "2026-05-30T00:00:00+00:00",
                            ),
                        )
                        connection.commit()
                    finally:
                        connection.close()

                    db.init_db()
                    loaded = db.conversation_get(1)
            finally:
                db._DB_PATH = original_db_path
                db._wal_initialized = original_wal_initialized

        self.assertEqual(loaded["scratchpad"], {"topic": "alpha"})
        self.assertEqual(loaded["datasets"], {"feed_items_raw": {"dataset_id": "ds_1", "count": 2}})


if __name__ == "__main__":
    unittest.main()