# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
"""Regression coverage for KoreRAG's manual and timed ingest scheduling."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


HERE             = Path(__file__).resolve().parent
KORE_DATA_ROOT   = HERE.parent
COMMON_CODE_ROOT = KORE_DATA_ROOT / "CommonCode"

for path in (HERE, KORE_DATA_ROOT, COMMON_CODE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app import server


class _OneSchedulerTick:
    """Return False once, then stop the scheduler's loop."""

    def __init__(self) -> None:
        self.calls = 0

    def wait(self, _seconds: float) -> bool:
        self.calls += 1
        return self.calls > 1


class RAGIngestSchedulerTests(unittest.TestCase):
    def test_daily_weekly_and_manual_due_rules(self) -> None:
        today = date(2026, 9, 21)

        self.assertFalse(server._is_schedule_due("manual", None, today))
        self.assertTrue(server._is_schedule_due("daily", None, today))
        self.assertFalse(server._is_schedule_due("daily", today, today))
        self.assertTrue(server._is_schedule_due("daily", date(2026, 9, 20), today))
        self.assertFalse(server._is_schedule_due("weekly", date(2026, 9, 15), today))
        self.assertTrue(server._is_schedule_due("weekly", date(2026, 9, 14), today))

    def test_scheduler_launches_only_due_timed_ingestors(self) -> None:
        descriptors = {
            "manual": {"managed_by": "ingestor", "schedule": "manual", "sync": {}},
            "daily":  {"managed_by": "ingestor", "schedule": "daily",  "sync": {"last_run": "2026-09-20"}},
            "weekly": {"managed_by": "ingestor", "schedule": "weekly", "sync": {"last_run": "2026-09-14"}},
            "recent": {"managed_by": "ingestor", "schedule": "weekly", "sync": {"last_run": "2026-09-15"}},
        }
        launched: list[str] = []

        class _Today(date):
            @classmethod
            def today(cls) -> date:
                return cls(2026, 9, 21)

        with (
            patch.object(server, "_registry_reload", return_value=False),
            patch.object(server, "_prune_finished_ingest_processes"),
            patch.object(server, "list_database_ids", return_value=list(descriptors)),
            patch.object(server, "get_descriptor", side_effect=descriptors.get),
            patch.object(server, "_launch_ingestor", side_effect=launched.append),
            patch.object(server, "date", _Today),
        ):
            server._run_ingest_scheduler(_OneSchedulerTick())

        self.assertEqual(launched, ["daily", "weekly"])

    def test_launch_ingestor_starts_a_manual_or_timed_run(self) -> None:
        descriptor = {"managed_by": "ingestor", "ingestor": "example"}

        class _Process:
            pid = 1234

            def poll(self):
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir     = Path(temp_dir)
            ingestor_dir = data_dir / "databases" / "example"
            ingestor_dir.mkdir(parents=True)
            (ingestor_dir / "ingest.py").write_text("# test", encoding="utf-8")
            (ingestor_dir / "example.json").write_text("{}", encoding="utf-8")

            with (
                patch.dict(server.cfg, {"data_dir": str(data_dir)}),
                patch.object(server, "get_descriptor", return_value=descriptor),
                patch.object(server, "_registry_reload"),
                patch.object(server, "invalidate_rag_processing_scripts"),
                patch.object(server, "_assign_to_job"),
                patch.object(server.subprocess, "Popen", return_value=_Process()) as popen,
            ):
                result = server._launch_ingestor("example")

        self.assertEqual(result["status"], "started")
        self.assertEqual(result["pid"], 1234)
        popen.assert_called_once()
        server._ingest_procs.pop("example", None)

    def test_launch_failure_marks_the_descriptor_failed(self) -> None:
        descriptor = {"managed_by": "ingestor", "ingestor": "example"}

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir     = Path(temp_dir)
            ingestor_dir = data_dir / "databases" / "example"
            ingestor_dir.mkdir(parents=True)
            (ingestor_dir / "ingest.py").write_text("# test", encoding="utf-8")
            descriptor_path = ingestor_dir / "example.json"
            descriptor_path.write_text("{}", encoding="utf-8")

            with (
                patch.dict(server.cfg, {"data_dir": str(data_dir)}),
                patch.object(server, "get_descriptor", return_value=descriptor),
                patch.object(server, "_registry_reload"),
                patch.object(server, "invalidate_rag_processing_scripts"),
                patch.object(server.subprocess, "Popen", side_effect=OSError("cannot start")),
            ):
                with self.assertRaises(OSError):
                    server._launch_ingestor("example")

            saved = json.loads(descriptor_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["sync"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
