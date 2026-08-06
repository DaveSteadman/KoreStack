# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Regression tests for portable durable-plan archive snapshots and lookup behaviour.
# ====================================================================================================

import sys
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch


APP_ROOT = Path(__file__).resolve().parents[4] / "KoreAgent" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import workflow_archives as archives
import workflow_store as planner_store


class WorkflowArchiveTests(unittest.TestCase):
    def test_export_and_unique_substring_lookup(self) -> None:
        plan = {"static": {"objective": "Romanian defence research", "tasks": []}, "dynamic": {"tasks": {}}}
        with tempfile.TemporaryDirectory() as temporary_dir:
            archive_dir = Path(temporary_dir) / "plans"
            with patch.object(archives, "get_plans_dir", return_value=archive_dir):
                exported = archives.export_plan_archive(name="Romanian Defence", plan=plan, source_conversation_id=71)
                resolved, matches = archives.resolve_plan_archive("defence")
                loaded = archives.load_plan_archive("Romanian Defence")

        self.assertEqual(exported["name"], "romanian-defence")
        self.assertEqual(matches, [])
        self.assertEqual(resolved.name, "romanian-defence.plan.json")
        self.assertRegex(loaded["archive"]["exported_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertNotIn("source", loaded["archive"])
        self.assertEqual(loaded["archive"]["plan"]["static"]["objective"], "Romanian defence research")
        self.assertNotIn("dynamic", loaded["archive"]["plan"])

    def test_ambiguous_substring_does_not_select_an_archive(self) -> None:
        plan = {"static": {"objective": "Research", "tasks": []}, "dynamic": {"tasks": {}}}
        with tempfile.TemporaryDirectory() as temporary_dir:
            archive_dir = Path(temporary_dir) / "plans"
            with patch.object(archives, "get_plans_dir", return_value=archive_dir):
                archives.export_plan_archive(name="Romanian Defence", plan=plan)
                archives.export_plan_archive(name="Romanian Procurement", plan=plan)
                resolved, matches = archives.resolve_plan_archive("romanian")

        self.assertIsNone(resolved)
        self.assertEqual([match.name for match in matches], ["romanian-defence.plan.json", "romanian-procurement.plan.json"])

    def test_load_accepts_older_archive_with_source_metadata(self) -> None:
        legacy_archive = {
            "format": "koreagent-plan",
            "format_version": 1,
            "exported_at": "2026-08-02T21:36:56.632755+00:00",
            "source": {
                "conversation_id": 71,
                "plan_revision": None,
            },
            "plan": {
                "static": {
                    "objective": "Romanian defence research",
                    "tasks": [
                        {
                            "id": "plantask_discover",
                            "title": "Discover",
                            "description": "",
                            "instruction": "Discover",
                            "depends_on": [],
                        }
                    ],
                }
            },
        }
        with tempfile.TemporaryDirectory() as temporary_dir:
            archive_dir = Path(temporary_dir) / "plans"
            archive_dir.mkdir(parents=True, exist_ok=True)
            legacy_path = archive_dir / "romanian-defence.plan.json"
            legacy_path.write_text(json.dumps(legacy_archive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with patch.object(archives, "get_plans_dir", return_value=archive_dir):
                loaded = archives.load_plan_archive("Romanian Defence")

        self.assertEqual(loaded["archive"]["source"]["conversation_id"], 71)
        self.assertEqual(loaded["archive"]["plan"]["static"]["objective"], "Romanian defence research")


if __name__ == "__main__":
    unittest.main()
