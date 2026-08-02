# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Regression tests for portable durable-plan archive snapshots and lookup behaviour.
# ====================================================================================================

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import indepth_planner_archives as archives
import indepth_planner_store as planner_store


class InDepthPlannerArchiveTests(unittest.TestCase):
    def test_export_and_unique_substring_lookup(self) -> None:
        plan = planner_store.build_plan_payload(objective="Romanian defence research", initial_tasks=[{"title": "Discover"}])
        with tempfile.TemporaryDirectory() as temporary_dir:
            archive_dir = Path(temporary_dir) / "plans"
            with patch.object(archives, "get_plans_dir", return_value=archive_dir):
                exported = archives.export_plan_archive(name="Romanian Defence", plan=plan, source_conversation_id=71)
                resolved, matches = archives.resolve_plan_archive("defence")
                loaded = archives.load_plan_archive("Romanian Defence")

        self.assertEqual(exported["name"], "romanian-defence")
        self.assertEqual(matches, [])
        self.assertEqual(resolved.name, "romanian-defence.plan.json")
        self.assertEqual(loaded["archive"]["source"]["conversation_id"], 71)
        self.assertEqual(loaded["archive"]["plan"]["current"]["objective"], "Romanian defence research")

    def test_ambiguous_substring_does_not_select_an_archive(self) -> None:
        plan = planner_store.build_plan_payload(objective="Research", initial_tasks=[{"title": "Discover"}])
        with tempfile.TemporaryDirectory() as temporary_dir:
            archive_dir = Path(temporary_dir) / "plans"
            with patch.object(archives, "get_plans_dir", return_value=archive_dir):
                archives.export_plan_archive(name="Romanian Defence", plan=plan)
                archives.export_plan_archive(name="Romanian Procurement", plan=plan)
                resolved, matches = archives.resolve_plan_archive("romanian")

        self.assertIsNone(resolved)
        self.assertEqual([match.name for match in matches], ["romanian-defence.plan.json", "romanian-procurement.plan.json"])


if __name__ == "__main__":
    unittest.main()
