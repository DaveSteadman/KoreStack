# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Regression tests for the durable KoreChat-backed Workflow lifecycle.
# ====================================================================================================

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


APP_ROOT = Path(__file__).resolve().parents[4] / "KoreAgent" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import workflow_store as planner_store
from skills_catalog_builder import build_skills_payload


class WorkflowStoreTests(unittest.TestCase):
    def test_catalog_rejects_function_bearing_skill_without_module(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_dir = Path(temp_dir) / "BrokenSkill"
            skill_dir.mkdir()
            (skill_dir / "skill.md").write_text(
                "# Broken Skill\n\n## Functions\n\n- `broken_tool()`\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "must declare a Module path"):
                build_skills_payload(Path(temp_dir), use_llm=False)

    def test_simple_plan_validation_rejects_missing_dependencies(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "dependency"):
            planner_store._validate_simple_plan(
                {"static": {"tasks": [{"id": "1", "depends_on": ["2"]}]}, "dynamic": {"tasks": {}}}
            )

    def test_clear_simple_task_data_retains_ran_flag(self) -> None:
        plan = {
            "static":  {"objective": "research", "tasks": [{"id": "1", "title": "Find", "description": "", "instruction": "Find", "depends_on": []}]},
            "dynamic": {"tasks": {"1": {"ran": True, "data": {"source": "example"}, "outputs": ["report.md"], "evidence": {"sources": 4}}}},
        }
        with (
            patch.object(planner_store, "get_simple_plan", return_value=plan),
            patch.object(planner_store, "_save_simple_plan", side_effect=lambda saved, **_kwargs: saved),
        ):
            saved = planner_store.clear_simple_task_data(task_id="1")

        self.assertTrue(saved["dynamic"]["tasks"]["1"]["ran"])
        self.assertEqual(saved["dynamic"]["tasks"]["1"]["data"], {})
        self.assertEqual(saved["dynamic"]["tasks"]["1"]["outputs"], [])
        self.assertEqual(saved["dynamic"]["tasks"]["1"]["evidence"], {})

    def test_simple_task_preserves_static_output_and_evidence_contract(self) -> None:
        saved: list[dict] = []

        def save(plan: dict, **_kwargs: object) -> dict:
            saved.append(plan)
            return plan

        with patch.object(planner_store, "_save_simple_plan", side_effect=save):
            planner_store.create_simple_plan(
                objective="Research",
                initial_tasks=[
                    {
                        "title": "Collect evidence",
                        "outputs": [
                            {"type": "file", "path": "reports/sources.txt", "minimum_bytes": 20},
                            {"type": "dataset", "name": "sources", "minimum_items": 10},
                        ],
                        "evidence_requirements": [
                            {"type": "unique_field_count", "dataset": "sources", "field": "url", "minimum": 10},
                        ],
                    }
                ],
            )

        task = saved[0]["static"]["tasks"][0]
        self.assertEqual(task["outputs"][0]["target"], "reports/sources.txt")
        self.assertEqual(task["outputs"][1]["target"], "sources")
        self.assertEqual(task["evidence_requirements"][0]["field"], "url")

    def test_record_task_result_is_dynamic_and_does_not_mark_task_ran(self) -> None:
        plan = {
            "static":  {"objective": "research", "tasks": [{"id": "1", "title": "Find", "description": "", "instruction": "Find", "depends_on": []}]},
            "dynamic": {"tasks": {}},
        }
        with (
            patch.object(planner_store, "get_simple_plan", return_value=plan),
            patch.object(planner_store, "_save_simple_plan", side_effect=lambda saved, **_kwargs: saved),
        ):
            saved = planner_store.record_simple_task_result(
                task_id="1",
                outputs=[{"path": "reports/sources.txt"}],
                evidence={"source_count": 12},
                note="Discovery run completed.",
            )

        state = saved["dynamic"]["tasks"]["1"]
        self.assertFalse(state["ran"])
        self.assertEqual(state["outputs"], [{"path": "reports/sources.txt"}])
        self.assertEqual(state["evidence"], {"source_count": 12})
        self.assertEqual(state["data"]["note"], "Discovery run completed.")

    def test_task_contract_checks_each_required_file_individually(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports = root / "reports"
            reports.mkdir()
            (reports / "sources.txt").write_text("source\n" * 8, encoding="utf-8")
            plan = {
                "static": {
                    "objective": "research",
                    "tasks": [
                        {
                            "id": "1",
                            "title": "Report",
                            "description": "",
                            "instruction": "Write reports.",
                            "depends_on": [],
                            "outputs": [
                                {"type": "file", "target": "reports/sources.txt", "minimum_bytes": 20},
                                {"type": "file", "target": "reports/report.md", "minimum_bytes": 20},
                            ],
                        }
                    ],
                },
                "dynamic": {"tasks": {}},
            }
            with (
                patch.object(planner_store, "get_simple_plan", return_value=plan),
                patch("KoreCommon.datauser_fs.resolve_datauser_path", side_effect=lambda target: root / target),
            ):
                gaps = planner_store.evaluate_simple_task_contract(task_id="1")

        self.assertEqual(gaps, ["Required output file 'reports/report.md' is missing or smaller than 20 bytes."])

    def test_reset_simple_task_run_retains_dynamic_data(self) -> None:
        plan = {
            "static":  {"objective": "research", "tasks": [{"id": "1", "title": "Find", "description": "", "instruction": "Find", "depends_on": []}]},
            "dynamic": {"tasks": {"1": {"ran": True, "data": {"source": "example"}}}},
        }
        with (
            patch.object(planner_store, "get_simple_plan", return_value=plan),
            patch.object(planner_store, "_save_simple_plan", side_effect=lambda saved, **_kwargs: saved),
        ):
            saved = planner_store.reset_simple_task_run(task_id="1")

        self.assertFalse(saved["dynamic"]["tasks"]["1"]["ran"])
        self.assertEqual(saved["dynamic"]["tasks"]["1"]["data"], {"source": "example"})

    def test_clear_simple_dynamic_preserves_static_plan(self) -> None:
        plan = {
            "static":  {"objective": "research", "tasks": [{"id": "1", "title": "Find", "description": "", "instruction": "Find", "depends_on": []}]},
            "dynamic": {"tasks": {"1": {"ran": True, "data": {"source": "example"}}}},
        }
        with (
            patch.object(planner_store, "get_simple_plan", return_value=plan),
            patch.object(planner_store, "_save_simple_plan", side_effect=lambda saved, **_kwargs: saved),
        ):
            saved = planner_store.clear_simple_dynamic()

        self.assertEqual(saved["static"]["tasks"][0]["id"], "1")
        self.assertEqual(saved["dynamic"], {"tasks": {}})

    def test_plan_assigns_human_facing_sequential_task_ids(self) -> None:
        payload = planner_store.build_plan_payload(
            objective="research",
            initial_tasks=[{"title": "Discover"}, {"title": "Analyse"}, {"title": "Report"}],
        )

        self.assertEqual([task["task_id"] for task in payload["current"]["tasks"]], ["1", "2", "3"])
        self.assertEqual(planner_store.summarize_plan(payload)["next"]["task_id"], "1")
        self.assertEqual(planner_store.summarize_plan(payload)["tasks"][1]["display_id"], "2")

    def test_plan_preserves_a_caller_supplied_task_id(self) -> None:
        payload = planner_store.build_plan_payload(
            objective="research",
            initial_tasks=[{"task_id": "research", "title": "Discover"}],
        )

        self.assertEqual(payload["current"]["tasks"][0]["task_id"], "research")

    def test_legacy_plan_task_can_be_found_by_its_one_based_position(self) -> None:
        tasks = [
            {"task_id": "plantask_discover", "definition": {"title": "Discover"}},
            {"task_id": "plantask_analyse", "definition": {"title": "Analyse"}},
        ]

        self.assertEqual(planner_store._find_task(tasks, "2")["task_id"], "plantask_analyse")

    def test_complete_plan_task_persists_evidence_and_completion_status(self) -> None:
        payload = planner_store.build_plan_payload(objective="research", initial_tasks=[{"title": "Discover"}])
        persisted: list[dict] = []

        def save_with_revision(updated: dict, **_kwargs: object) -> dict:
            persisted.append(updated)
            return updated

        with (
            patch.object(planner_store, "get_plan", return_value=payload),
            patch.object(planner_store, "_save_with_revision", side_effect=save_with_revision),
        ):
            planner_store.complete_plan_task(
                task_id        = "1",
                result_summary = "Retrieved and assessed the relevant source material.",
                output_refs    = [{"dataset": "koredata_search_1"}],
            )

        task = persisted[0]["current"]["tasks"][0]
        self.assertEqual(task["execution"]["status"], "completed")
        self.assertEqual(task["execution"]["result_summary"], "Retrieved and assessed the relevant source material.")
        self.assertEqual(task["execution"]["output_refs"], [{"dataset": "koredata_search_1"}])

    def test_create_plan_replaces_the_entire_existing_plan(self) -> None:
        conversation = {"id": 71, "workflow": {"current": {"objective": "old plan"}}}
        persisted: list[dict] = []

        def patch_plan(_base: str, _path: str, payload: dict) -> dict:
            persisted.append(payload["workflow"])
            return {"id": 71, "workflow": payload["workflow"]}

        with (
            patch.object(planner_store, "ensure_conversation_for_session", return_value=conversation),
            patch.object(planner_store, "_get_korechat_base", return_value="http://korechat"),
            patch.object(planner_store, "_http_patch", side_effect=patch_plan),
        ):
            saved = planner_store.create_plan(
                objective="new plan",
                initial_tasks=[{"title": "new task"}],
                session_id="session_71",
            )

        self.assertEqual(saved["current"]["objective"], "new plan")
        self.assertEqual(len(saved["current"]["tasks"]), 1)
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["static"]["objective"], "new plan")
        self.assertIn("dynamic", persisted[0])
        self.assertNotIn("old plan", str(persisted[0]))

    def test_save_rejects_a_korechat_service_that_drops_plans(self) -> None:
        with (
            patch.object(planner_store, "ensure_conversation_for_session", return_value={"id": 71}),
            patch.object(planner_store, "_get_korechat_base", return_value="http://korechat"),
            patch.object(planner_store, "_http_patch", return_value={"id": 71}),
        ):
            with self.assertRaisesRegex(RuntimeError, "did not return the workflow field"):
                planner_store.save_indepth_planner({"current": {"objective": "new plan"}}, session_id="session_71")

    def test_clear_plan_replaces_the_persisted_plan_with_an_empty_object(self) -> None:
        persisted: list[dict] = []

        def patch_plan(_base: str, _path: str, payload: dict) -> dict:
            persisted.append(payload["workflow"])
            return {"id": 71, "workflow": payload["workflow"]}

        with (
            patch.object(planner_store, "ensure_conversation_for_session", return_value={"id": 71}),
            patch.object(planner_store, "_get_korechat_base", return_value="http://korechat"),
            patch.object(planner_store, "_http_patch", side_effect=patch_plan),
        ):
            planner_store.clear_plan(session_id="session_71")

        self.assertEqual(persisted, [{}])

    def test_nested_persisted_task_retains_its_definition_and_execution_on_update(self) -> None:
        payload = planner_store.build_plan_payload(
            objective="research",
            initial_tasks=[
                {
                    "title": "Information Discovery via KoreData Search",
                    "task_statement": "Search KoreData for Romanian defence information.",
                }
            ],
        )
        persisted: list[dict] = []

        def save_with_revision(updated: dict, **_kwargs: object) -> dict:
            persisted.append(updated)
            return updated

        task_id = payload["current"]["tasks"][0]["task_id"]
        with (
            patch.object(planner_store, "get_plan", return_value=payload),
            patch.object(planner_store, "_save_with_revision", side_effect=save_with_revision),
        ):
            planner_store.set_plan_task_status(
                task_id=task_id,
                status="active",
                session_id="session_71",
            )

        task = persisted[0]["current"]["tasks"][0]
        self.assertEqual(task["definition"]["title"], "Information Discovery via KoreData Search")
        self.assertEqual(task["definition"]["task_statement"], "Search KoreData for Romanian defence information.")
        self.assertEqual(task["execution"]["status"], "active")
        self.assertEqual(planner_store.summarize_plan(payload)["next"], None)


if __name__ == "__main__":
    unittest.main()
