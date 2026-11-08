# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Test coverage for kiwix importer.
# Exercises the expected behaviour and regression boundaries for this area.
# MARK: FUNCTIONS
# Primary types: KiwixImporterTests.
# Function inventory:
# - _null_db_connection: Implements the  null db connection operation for this module.
# - _null_http_client: Implements the  null http client operation for this module.
# - test_resume_existing_seed_counts_as_done: Implements the test resume existing seed counts as done operation for this module.
# ====================================================================================================

import sys
import sqlite3
import threading
import unittest
from contextlib import contextmanager
import os
from pathlib import Path
from unittest.mock import patch


SERVICE_ROOT = Path(os.environ["KORESTACK_ROOT"]) / "KoreData" / "KoreReference"
COMMON_CODE_ROOT = Path(os.environ["KORESTACK_ROOT"]) / "KoreData" / "CommonCode"
if str(COMMON_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_CODE_ROOT))
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.importers import kiwix  # noqa: E402
from app.importers import state
from app import database


@contextmanager
def _null_db_connection():
    yield object()


@contextmanager
def _null_http_client():
    yield object()


class KiwixImporterTests(unittest.TestCase):
    def test_stop_during_resolution_keeps_committed_batches(self) -> None:
        stop = threading.Event()
        progress = []
        with sqlite3.connect(":memory:") as conn:
            conn.executescript("""
                CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT);
                CREATE TABLE links (from_id INTEGER, to_title TEXT, to_id INTEGER);
                INSERT INTO articles VALUES (1, 'Known');
                INSERT INTO links VALUES (1, 'Known', NULL), (1, 'Known', NULL);
            """)

            @contextmanager
            def connection():
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

            def report(checked, resolved):
                progress.append((checked, resolved))
                stop.set()

            with patch.object(database, "db_connection", connection):
                self.assertEqual(database.resolve_links(
                    batch_size=1, should_stop=stop.is_set, on_progress=report,
                ), 1)
                self.assertEqual(progress, [(1, 1)])
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM links WHERE to_id IS NULL").fetchone()[0], 1,
                )
                stop.clear()
                self.assertEqual(database.resolve_links(should_stop=stop.is_set), 1)

    def test_stop_interrupts_sqlite_resolution_inside_a_batch(self) -> None:
        stop = threading.Event()
        with sqlite3.connect(":memory:") as conn:
            conn.executescript("""
                CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT);
                CREATE TABLE links (from_id INTEGER, to_title TEXT, to_id INTEGER);
                INSERT INTO articles VALUES (1, 'Known');
            """)
            conn.executemany("INSERT INTO links VALUES (1, 'Known', NULL)", [()] * 10_000)
            conn.commit()
            lookups = 0

            def cancel_on_lookup(value):
                nonlocal lookups
                lookups += 1
                if lookups == 10:
                    stop.set()
                return value.lower()

            conn.create_function("lower", 1, cancel_on_lookup)

            @contextmanager
            def connection():
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

            with patch.object(database, "db_connection", connection):
                self.assertEqual(database.resolve_links(
                    batch_size=10_000, should_stop=stop.is_set,
                ), 0)
            self.assertLess(lookups, 500)
            # The cancellation handler must be removed so this query still works.
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM links").fetchone()[0], 10_000)

    def test_resolution_does_not_rescan_missing_prefix_for_every_batch(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            lookups = 0

            def counted_lower(value):
                nonlocal lookups
                lookups += 1
                if lookups > 100_000:
                    raise AssertionError("Repeatedly scanning previously examined links")
                return value.lower()

            conn.create_function("lower", 1, counted_lower, deterministic=True)
            conn.executescript("""
                CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT);
                CREATE INDEX idx_articles_title_lower ON articles (lower(title));
                CREATE TABLE links (from_id INTEGER, to_title TEXT, to_id INTEGER);
                CREATE INDEX idx_links_to ON links (to_id);
                INSERT INTO articles VALUES (1, 'Known');
            """)
            conn.executemany("INSERT INTO links VALUES (1, ?, NULL)",
                             [("Missing",)] * 20_000 + [("Known",)] * 2_000)
            lookups = 0

            @contextmanager
            def connection():
                yield conn
                conn.commit()

            with patch.object(database, "db_connection", connection):
                self.assertEqual(database.resolve_links(batch_size=100), 2_000)
            self.assertLess(lookups, 100_000)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM links WHERE to_id IS NULL").fetchone()[0],
                20_000,
            )

    def test_prefix_and_backfill_do_not_publish_completion(self) -> None:
        original_state = dict(state.import_state)
        original_stop = state.import_stop_event.is_set()

        def resolve(**kwargs):
            self.assertTrue(state.import_state["running"])
            self.assertEqual(state.import_state["phase"], "finalizing")

        try:
            state.import_stop_event.clear()
            with patch.object(kiwix, "db_connection", _null_db_connection), \
                 patch.object(kiwix, "_http_client", _null_http_client), \
                 patch.object(kiwix, "suggest_titles", return_value=[]), \
                 patch.object(kiwix, "get_unresolved_link_titles", return_value=[]), \
                 patch.object(kiwix, "resolve_links", side_effect=resolve) as resolver:
                for worker, args in (
                    (kiwix.run_kiwix_import, ("test", "http://localhost:8080", None, "", 1, True)),
                    (kiwix.run_kiwix_backfill, ("test", "http://localhost:8080", 1)),
                ):
                    with self.subTest(worker=worker.__name__):
                        state.import_state.update({"running": True, "phase": "importing"})
                        worker(*args)
                        self.assertTrue(state.import_state["running"])
                self.assertEqual(resolver.call_count, 2)
        finally:
            state.import_state.clear()
            state.import_state.update(original_state)
            if original_stop:
                state.import_stop_event.set()
            else:
                state.import_stop_event.clear()

    def test_resolve_links_terminates_with_missing_targets(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            conn.executescript("""
                CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT);
                CREATE TABLE links (from_id INTEGER, to_title TEXT, to_id INTEGER);
                INSERT INTO articles VALUES (1, 'Known'), (2, 'Other');
                INSERT INTO links VALUES
                    (1, 'Missing', NULL), (1, 'known', NULL),
                    (1, 'Other', NULL), (1, 'Still missing', NULL);
            """)
            batches = 0

            @contextmanager
            def connection():
                nonlocal batches
                batches += 1
                if batches > 12:
                    raise AssertionError("Link resolution keeps revisiting missing targets")
                yield conn
                conn.commit()

            with patch.object(database, "db_connection", connection):
                self.assertEqual(database.resolve_links(batch_size=1), 2)
                self.assertEqual(database.resolve_links(batch_size=1), 0)
            self.assertEqual(
                conn.execute("SELECT to_id FROM links ORDER BY rowid").fetchall(),
                [(None,), (1,), (2,), (None,)],
            )

    def test_worker_stays_running_until_link_resolution_finishes(self) -> None:
        original_state = dict(state.import_state)
        original_stop = state.import_stop_event.is_set()
        resolving = threading.Event()
        release = threading.Event()

        def resolve(**kwargs):
            resolving.set()
            if not release.wait(timeout=3):
                raise AssertionError("Test did not release link resolution")

        try:
            with patch.object(kiwix, "db_connection", _null_db_connection), \
                 patch.object(kiwix, "_http_client", _null_http_client), \
                 patch.object(kiwix, "get_article_by_title", return_value={"title": "Existing"}), \
                 patch.object(kiwix, "resolve_links", resolve):
                self.assertTrue(state.start_import_worker(
                    target=kiwix.run_kiwix_crawl,
                    args=("https://en.wikipedia.org/wiki/Existing", 0, 1, 0.0, True),
                    name="test-crawl-finalization",
                    initial_state={"done": 0, "errors": 0, "last_error": None},
                ))
                try:
                    self.assertTrue(resolving.wait(timeout=2))
                    with state.state_lock:
                        self.assertTrue(state.import_state["running"])
                    self.assertFalse(state.start_import_worker(
                        target=lambda: None, args=(), name="overlap", initial_state={},
                    ))
                finally:
                    release.set()
                    self.assertTrue(state.import_lock.acquire(timeout=3))
                    state.import_lock.release()
                self.assertFalse(state.import_state["running"])
                self.assertTrue(state.start_import_worker(
                    target=lambda: None, args=(), name="next-import", initial_state={},
                ))
                self.assertTrue(state.import_lock.acquire(timeout=3))
                state.import_lock.release()
        finally:
            with state.state_lock:
                state.import_state.clear()
                state.import_state.update(original_state)
            if original_stop:
                state.import_stop_event.set()
            else:
                state.import_stop_event.clear()

    def test_resume_existing_seed_counts_as_done(self) -> None:
        original_parse_seed_url       = kiwix.parse_seed_url
        original_db_connection       = kiwix.db_connection
        original_http_client         = kiwix._http_client
        original_get_article_by_title = kiwix.get_article_by_title
        original_get_links           = kiwix.get_links
        original_resolve_links       = kiwix.resolve_links
        original_import_state        = dict(kiwix.import_state)
        original_stop_event_state    = kiwix.import_stop_event.is_set()
        result_state                 = None

        try:
            kiwix.parse_seed_url        = lambda seed_url: ("wikipedia", "https://en.wikipedia.org", "en.wikipedia.org", "Existing Title")
            kiwix.db_connection         = _null_db_connection
            kiwix._http_client          = _null_http_client
            kiwix.get_article_by_title  = lambda title, full=False: {"title": title}
            kiwix.get_links             = lambda title: []
            kiwix.resolve_links         = lambda **kwargs: None

            kiwix.import_stop_event.clear()
            kiwix.import_state.clear()
            kiwix.import_state.update({
                "running":           True,
                "done":              0,
                "total":             0,
                "limit":             0,
                "errors":            0,
                "last_error":        None,
                "mode":              "crawl",
                "seed":              "Existing Title",
                "delay_seconds":     0.0,
                "redirects_stored":  0,
                "last_redirect":     None,
            })

            kiwix.run_kiwix_crawl(
                "https://en.wikipedia.org/wiki/Existing_Title",
                max_depth     = 0,
                limit         = 1,
                delay_seconds = 0.0,
                resume        = True,
            )
            result_state = dict(kiwix.import_state)
        finally:
            kiwix.parse_seed_url        = original_parse_seed_url
            kiwix.db_connection         = original_db_connection
            kiwix._http_client          = original_http_client
            kiwix.get_article_by_title  = original_get_article_by_title
            kiwix.get_links             = original_get_links
            kiwix.resolve_links         = original_resolve_links
            kiwix.import_state.clear()
            kiwix.import_state.update(original_import_state)
            if original_stop_event_state:
                kiwix.import_stop_event.set()
            else:
                kiwix.import_stop_event.clear()

        self.assertIsNotNone(result_state)
        self.assertEqual(result_state["done"], 1)
        # Only the managed worker may publish completion and release admission.
        self.assertTrue(result_state["running"])
        self.assertEqual(result_state["phase"], "finalizing")
        self.assertEqual(result_state["errors"], 0)


if __name__ == "__main__":
    unittest.main()
