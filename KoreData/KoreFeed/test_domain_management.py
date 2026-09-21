# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Regression coverage for KoreFeed domain naming and artifact deletion.
# ====================================================================================================

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


HERE             = Path(__file__).resolve().parent
KORE_DATA_ROOT   = HERE.parent
COMMON_CODE_ROOT = KORE_DATA_ROOT / "CommonCode"

for path in (HERE, KORE_DATA_ROOT, COMMON_CODE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["KOREDATA_DATA_DIR"] = _TMP_DIR.name

from app import database, feed_manager, ingest


class DomainManagementTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        database.release_all_cached_connections()
        _TMP_DIR.cleanup()

    def setUp(self) -> None:
        database.release_all_cached_connections()
        for path in Path(_TMP_DIR.name).iterdir():
            if path.is_file():
                path.unlink()

    def test_legacy_dot_db_deletion_does_not_delete_underscore_db(self) -> None:
        legacy_path = database.DATA_DIR / ".db"
        sqlite3.connect(legacy_path).close()
        sqlite3.connect(database.DATA_DIR / "_db.db").close()
        database._domains_ready.add("")
        underscore_path = database.DATA_DIR / "_db.db"

        self.assertTrue(database.delete_domain_db(".db"))
        self.assertFalse(legacy_path.exists())
        self.assertTrue(underscore_path.exists())
        self.assertNotIn("", database._domains_ready)

    def test_domain_deletion_removes_sqlite_sidecars_and_ready_state(self) -> None:
        domain = "DeleteMe"
        database.init_db(domain)
        db_path = database.get_db_path(domain)
        for suffix in ("-wal", "-shm"):
            Path(f"{db_path}{suffix}").touch()

        self.assertTrue(database.delete_domain_db(domain))
        self.assertFalse(db_path.exists())
        self.assertFalse(Path(f"{db_path}-wal").exists())
        self.assertFalse(Path(f"{db_path}-shm").exists())
        self.assertNotIn(domain, database._domains_ready)

    def test_invalid_domain_names_cannot_create_domain_artifacts(self) -> None:
        for invalid_name in ("", ".db", "_db", "has.dot", "path/name"):
            with self.assertRaises(ValueError):
                feed_manager.create_domain(invalid_name)
            with self.assertRaises(ValueError):
                feed_manager.add_feed(invalid_name, "Example", "https://example.test/rss", 60)
        for invalid_name in ("", "has.dot", "path/name"):
            with self.assertRaises(database.FeedDatabaseError):
                database.get_db_path(invalid_name)

        self.assertEqual(list(feed_manager.FEEDS_DIR.glob("*.json")), [])
        # Other test modules share this configured temporary data root, so
        # assert only that an invalid name did not create its legacy alias.
        self.assertFalse((database.DATA_DIR / "_db.db").exists())

    def test_legacy_underscore_domain_artifacts_are_deletable(self) -> None:
        feed_path  = feed_manager.FEEDS_DIR / "_db.json"
        state_path = feed_manager.FEEDS_DIR / "_db.state.json"
        db_path    = database.DATA_DIR / "_db.db"
        feed_manager.FEEDS_DIR.mkdir(exist_ok=True)
        feed_path.write_text('{"domain":".db","feeds":[]}', encoding="utf-8")
        state_path.write_text("{}", encoding="utf-8")
        sqlite3.connect(db_path).close()

        self.assertIn("_db", feed_manager.list_feed_domains())
        self.assertNotIn("_db", [feed["domain"] for feed in feed_manager.load_feeds()])
        self.assertTrue(feed_manager.delete_domain_feeds("_db"))
        self.assertTrue(database.delete_domain_db("_db"))
        self.assertFalse(feed_path.exists())
        self.assertFalse(state_path.exists())
        self.assertFalse(db_path.exists())

    def test_inflight_ingest_does_not_write_after_feed_removal(self) -> None:
        feed = {"id": "removed-feed", "domain": "DeleteMe"}
        with patch.object(ingest, "get_feed", return_value=None):
            self.assertFalse(ingest._feed_is_current(feed))

    def test_atomic_feed_write_retries_a_transient_dropbox_lock(self) -> None:
        target = feed_manager.FEEDS_DIR / "retry-state.json"
        with (
            patch.object(feed_manager.os, "replace", side_effect=[PermissionError("locked"), None]) as replace,
            patch.object(feed_manager.time, "sleep") as sleep,
        ):
            feed_manager._write_json_atomic(target, {"ok": True})

        self.assertEqual(replace.call_count, 2)
        sleep.assert_called_once_with(feed_manager._ATOMIC_REPLACE_DELAY_S)


if __name__ == "__main__":
    unittest.main()
