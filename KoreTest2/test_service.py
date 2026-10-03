from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from KoreTest2 import service


class KoreTest2ServiceTests(unittest.TestCase):
    def test_discovers_one_case_per_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cases_dir = Path(temp_dir)
            (cases_dir / "KoreData_FeedTest_001.md").write_text(
                "# KoreData_FeedTest_001\n\nFind feed items.\n\n```json\n{\"evaluation\": {\"type\": \"python\", \"assert\": \"not_empty\"}}\n```\n",
                encoding="utf-8",
            )
            with patch.object(service, "CASES_DIR", cases_dir):
                found = service.cases()
        self.assertEqual(found[0]["id"], "KoreData_FeedTest_001")
        self.assertEqual(found[0]["prompt"], "Find feed items.")

    def test_result_is_keyed_by_test_and_build(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case = {"id": "KoreData_FeedTest_001", "prompt": "hello", "spec": {"evaluation": {"type": "python", "assert": "not_empty"}}}
            with patch.object(service, "DATA_ROOT", root), patch.object(service, "RUNS_DIR", root / "runs"), patch.object(service, "DB_PATH", root / "results.sqlite3"), patch.object(service, "_invoke", return_value={"response": "ok", "run_id": "run"}):
                service._run_case(case, "Build 1", service.time.monotonic() + 60)
                service._run_case(case, "Build 1", service.time.monotonic() + 60)
                conn = service._db()
                count = conn.execute("SELECT COUNT(*) FROM results WHERE test_id=? AND build_id=?", (case["id"], "Build 1")).fetchone()[0]
                conn.close()
        self.assertEqual(count, 1)

    def test_grid_keeps_build_history(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cases_dir = root / "cases"
            cases_dir.mkdir()
            (cases_dir / "KoreData_FeedTest_001.md").write_text(
                "# KoreData_FeedTest_001\n\nprompt\n\n```json\n"
                '{"evaluation": {"type": "python", "assert": "not_empty"}}\n```\n',
                encoding="utf-8",
            )
            with (
                patch.object(service, "DATA_ROOT", root),
                patch.object(service, "CASES_DIR", cases_dir),
                patch.object(service, "RUNS_DIR", root / "runs"),
                patch.object(service, "DB_PATH", root / "results.sqlite3"),
                patch.object(service, "build_id", return_value="Build 2"),
                patch.object(service, "_invoke", return_value={"response": "ok", "run_id": "run"}),
            ):
                case = service.cases()[0]
                service._run_case(case, "Build 1", service.time.monotonic() + 60)
                service._run_case(case, "Build 2", service.time.monotonic() + 60)
                result = service.grid()
        self.assertEqual(result["builds"], ["Build 2", "Build 1"])
        self.assertEqual(result["tests"][0]["results"]["Build 1"]["status"], "passed")

    def test_builtin_assertions_match_legacy_cases(self) -> None:
        self.assertTrue(service._evaluate_assert("all_contains|first||second", "The first and second result"))
        self.assertTrue(service._evaluate_assert("none_contains|error||failed", "The result passed"))
        self.assertTrue(service._evaluate_assert("not_regex|error", "The result passed"))
        self.assertFalse(service._evaluate_assert("not_contains|error", "An error occurred"))

    def test_summary_counts_the_current_build(self) -> None:
        with patch.object(service, "grid", return_value={
            "build_id": "Build 1",
            "active": True,
            "tests": [
                {"results": {"Build 1": {"status": "passed"}}},
                {"results": {"Build 1": {"status": "timeout"}}},
                {"results": {}},
            ],
        }):
            result = service.summary()
        self.assertEqual(result, {"build_id": "Build 1", "active": True, "total": 3, "passed": 1, "failed": 1, "pending": 1})

    def test_bootstrap_copies_legacy_exchange_to_an_individual_case(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = root / "legacy"
            legacy.mkdir()
            (legacy / "test_koredata_feed.json").write_text(
                '[{"exchange": "feed", "turns": [{"user": "find feeds", "assert": "not_empty"}]}]',
                encoding="utf-8",
            )
            with patch.object(service, "CASES_DIR", root / "cases"), patch.object(service, "LEGACY_CASES_DIR", legacy):
                service._bootstrap_legacy_cases()
                imported = service.cases()
        self.assertEqual(imported[0]["id"], "KoreDataFeed_001")
        self.assertEqual(imported[0]["spec"]["evaluation"]["assertions"], ["not_empty"])
