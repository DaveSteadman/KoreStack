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
            (cases_dir / "KoreData_FeedTest_001.json").write_text(
                '{"prompt": "Find feed items.", "evaluation": {"type": "python", "assert": "not_empty"}}',
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
            (cases_dir / "KoreData_FeedTest_001.json").write_text(
                '{"prompt": "prompt", "evaluation": {"type": "python", "assert": "not_empty"}}',
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

    def test_numeric_comparison_accepts_any_number_format(self) -> None:
        for text in ("12! = 479,001,600", "**479001600**", "4.79001600e8", "479001600.0", "479_001_600"):
            self.assertTrue(service._evaluate_assert("number_equals|479001600", text), text)
        self.assertFalse(service._evaluate_assert("number_equals|479001600", "The answer is 479001601"))
        self.assertTrue(service._evaluate_assert("number_equals|3.14||0.01", "pi is about 3.141"))
        self.assertTrue(service._evaluate_assert("all_numbers|0||1||13", "0, 1, 1, 2, 3, 5, 8, 13"))
        self.assertFalse(service._evaluate_assert("all_numbers|0||21", "0, 1, 1, 2"))
        self.assertTrue(service.numbers_equal("1,000", "1e3"))
        self.assertEqual(service.extract_numbers("2026-10-03"), [2026, 10, 3])

    def test_asserts_list_requires_every_assert_to_pass(self) -> None:
        case = {"id": "x", "prompt": "What is 2+2?", "spec": {"evaluation": {"type": "python", "asserts": ["number_equals|4", "not_contains|error"]}}}
        self.assertTrue(service._evaluate(case, "The answer is 4")[0])
        passed, detail = service._evaluate(case, "4 error")
        self.assertFalse(passed)
        self.assertEqual([item["passed"] for item in detail["asserts"]], [True, False])

    def test_judge_assert_uses_probability_threshold(self) -> None:
        case = {"id": "x", "prompt": "Capital of France?", "spec": {"evaluation": {"type": "python", "asserts": ["judge|answers", "judge|not_error||0.9"]}}}
        with patch.object(service, "_judge", side_effect=[0.95, 0.8]) as judge:
            passed, detail = service._evaluate(case, "Paris")
        self.assertFalse(passed)
        self.assertEqual([item["passed"] for item in detail["asserts"]], [True, False])
        self.assertEqual(detail["asserts"][1]["probability"], 0.8)
        self.assertIn("answer the prompt", judge.call_args_list[0].args[0])
        self.assertEqual(judge.call_args_list[0].args[1:], ("Capital of France?", "Paris"))

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
