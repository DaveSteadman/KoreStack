# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Regression coverage for saved KoreData search filter round-tripping.
# ====================================================================================================

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


HERE             = Path(__file__).resolve().parent
COMMON_CODE_ROOT = HERE.parent / "CommonCode"

for path in (HERE, COMMON_CODE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["KOREDATA_DATA_DIR"] = _TMP_DIR.name

from app.gateway_api import SearchRequest
from app.server import _saved_search_payload


class SavedSearchFieldTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        _TMP_DIR.cleanup()

    def test_saved_payload_preserves_all_search_filter_fields(self) -> None:
        search = SearchRequest(
            query      = "air defence",
            domains    = ["feeds", "reference"],
            days_limit = 14,
            since      = "2026-08-26",
            until      = "2026-09-09",
            mode       = "semantic",
            min_match  = 0.65,
            limit      = 75,
        )

        self.assertEqual(
            _saved_search_payload(search),
            {
                "query":      "air defence",
                "domains":    ["feeds", "reference"],
                "since":      "2026-08-26",
                "until":      "2026-09-09",
                "days_limit": 14,
                "mode":       "semantic",
                "min_match":  0.65,
                "limit":      75,
            },
        )

    def test_saved_search_ui_restores_all_filter_controls(self) -> None:
        template = (
            HERE.parents[1]
            / "KoreUI"
            / "KoreData"
            / "KoreDataGateway"
            / "templates"
            / "home.html"
        ).read_text(encoding="utf-8")

        for expected in (
            'id="sq-days-limit"',
            "search.days_limit ?? ''",
            "search.since || ''",
            "search.until || ''",
            "search.min_match ?? '0.40'",
            "search.limit ?? 20",
        ):
            self.assertIn(expected, template)


if __name__ == "__main__":
    unittest.main()
