# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Unit tests for KoreTest result-artifact retention.
# ====================================================================================================

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from KoreTest.app.result_retention import prune_test_results


class ResultRetentionTests(unittest.TestCase):
    def test_prunes_old_runs_and_runs_outside_the_per_suite_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "test_results"
            root.mkdir()
            current = root / "2026-09-12"
            current.mkdir()
            old = root / "2026-06-01"
            old.mkdir()

            for day in range(20, 31):
                stamp = f"202608{day:02d}_080000"
                (current / f"test_results_{stamp}_default.csv").write_text("csv", encoding="utf-8")
                (current / f"summary_{stamp}_default.md").write_text("summary", encoding="utf-8")
            (current / "test_results_20260820_080000_default_analysis.csv").write_text("analysis", encoding="utf-8")

            old_csv  = old / "test_results_20260601_080000_rare.csv"
            old_md   = old / "summary_20260601_080000_rare.md"
            old_gaps = old / "test_results_20260601_080000_rare_gaps.txt"
            for path in (old_csv, old_md, old_gaps):
                path.write_text("old", encoding="utf-8")
            unknown = old / "notes.txt"
            unknown.write_text("retain", encoding="utf-8")

            report = prune_test_results(
                root,
                now=datetime(2026, 9, 12, 12, 0, 0),
                dry_run=True,
            )
            self.assertEqual(report["deleted"], 6)
            self.assertTrue(old_csv.exists())

            report = prune_test_results(root, now=datetime(2026, 9, 12, 12, 0, 0))
            self.assertEqual(report["deleted"], 6)
            self.assertGreater(report["bytes_reclaimed"], 0)
            self.assertFalse((current / "test_results_20260820_080000_default.csv").exists())
            self.assertFalse((current / "summary_20260820_080000_default.md").exists())
            self.assertFalse((current / "test_results_20260820_080000_default_analysis.csv").exists())
            self.assertFalse(old_csv.exists())
            self.assertFalse(old_md.exists())
            self.assertFalse(old_gaps.exists())
            self.assertTrue(unknown.exists())

    def test_rejects_an_invalid_run_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                prune_test_results(Path(temporary), run_limit=0)
