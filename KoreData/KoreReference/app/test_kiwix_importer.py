import sys
import unittest
from contextlib import contextmanager
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
COMMON_CODE_ROOT = Path(__file__).resolve().parents[2] / "CommonCode"
if str(COMMON_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_CODE_ROOT))
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.importers import kiwix  # noqa: E402


@contextmanager
def _null_db_connection():
    yield object()


@contextmanager
def _null_http_client():
    yield object()


class KiwixImporterTests(unittest.TestCase):
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
            kiwix.resolve_links         = lambda: None

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
        self.assertFalse(result_state["running"])
        self.assertEqual(result_state["errors"], 0)


if __name__ == "__main__":
    unittest.main()
