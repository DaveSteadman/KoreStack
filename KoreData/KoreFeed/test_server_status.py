"""Regression coverage for the KoreFeed status contract consumed by KoreDataGateway."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
KORE_DATA_ROOT = HERE.parent
COMMON_CODE_ROOT = KORE_DATA_ROOT / "CommonCode"

for path in (HERE, KORE_DATA_ROOT, COMMON_CODE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["KOREDATA_DATA_DIR"] = _TMP_DIR.name

from app import server


class FeedStatusTests(unittest.TestCase):
    def test_status_includes_gateway_card_totals(self) -> None:
        overview = {
            "domains": [],
            "all_feeds": [],
            "total_domains": 3,
            "total_feeds": 8,
            "total_entries": 144,
        }
        runtime = {"scheduler_running": True}

        with (
            patch.object(server, "get_feed_overview", return_value=overview),
            patch.object(server, "get_runtime_status", return_value=runtime),
        ):
            result = server.api_status()

        self.assertEqual(result["total_domains"], 3)
        self.assertEqual(result["total_feeds"], 8)
        self.assertEqual(result["total_entries"], 144)
        self.assertEqual(result["runtime"], runtime)


if __name__ == "__main__":
    unittest.main()
