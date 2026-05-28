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


if __name__ == "__main__":
    unittest.main()