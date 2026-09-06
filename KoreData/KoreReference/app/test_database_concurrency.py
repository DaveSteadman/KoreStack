# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Regression tests for KoreReference's independent SQLite connection model.
# ====================================================================================================

import sys
import tempfile
import unittest
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT    = Path(__file__).resolve().parents[3]
COMMON_CODE_ROOT = REPO_ROOT / "KoreData" / "CommonCode"
for path in (REPO_ROOT, SERVICE_ROOT, COMMON_CODE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app import database  # noqa: E402


class DatabaseConcurrencyTests(unittest.TestCase):
    def test_reader_observes_committed_snapshot_while_writer_is_open(self) -> None:
        original_data_dir = database.DATA_DIR
        original_db_path  = database._DB_PATH
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                database.DATA_DIR = Path(temp_dir)
                database._DB_PATH = database.DATA_DIR / "reference.db"
                database.init_db()

                with database.db_connection() as writer:
                    writer.execute("INSERT INTO articles (title) VALUES (?)", ("Uncommitted",))
                    with database.db_connection() as reader:
                        self.assertIsNot(writer, reader)
                        self.assertEqual(
                            reader.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
                            0,
                        )
                    writer.commit()

                with database.db_connection() as reader:
                    self.assertEqual(
                        reader.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
                        1,
                    )
        finally:
            database.DATA_DIR = original_data_dir
            database._DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
