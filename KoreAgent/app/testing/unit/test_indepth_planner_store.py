# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Regression tests for the durable KoreChat-backed InDepthPlanner lifecycle.
# ====================================================================================================

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import indepth_planner_store as planner_store


class InDepthPlannerStoreTests(unittest.TestCase):
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
        conversation = {"id": 71, "indepth_planner": {"current": {"objective": "old plan"}}}
        persisted: list[dict] = []

        def patch_plan(_base: str, _path: str, payload: dict) -> dict:
            persisted.append(payload["indepth_planner"])
            return {"id": 71, "indepth_planner": payload["indepth_planner"]}

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

        self.assertEqual(saved["baseline"]["objective"], "new plan")
        self.assertEqual(saved["current"]["objective"], "new plan")
        self.assertEqual(len(saved["current"]["tasks"]), 1)
        self.assertEqual(saved["current"]["revision"], 1)
        self.assertEqual(len(persisted), 1)
        self.assertNotIn("old plan", str(persisted[0]))

    def test_save_rejects_a_korechat_service_that_drops_plans(self) -> None:
        with (
            patch.object(planner_store, "ensure_conversation_for_session", return_value={"id": 71}),
            patch.object(planner_store, "_get_korechat_base", return_value="http://korechat"),
            patch.object(planner_store, "_http_patch", return_value={"id": 71}),
        ):
            with self.assertRaisesRegex(RuntimeError, "did not return the indepth_planner field"):
                planner_store.save_indepth_planner({"current": {"objective": "new plan"}}, session_id="session_71")

    def test_clear_plan_replaces_the_persisted_plan_with_an_empty_object(self) -> None:
        persisted: list[dict] = []

        def patch_plan(_base: str, _path: str, payload: dict) -> dict:
            persisted.append(payload["indepth_planner"])
            return {"id": 71, "indepth_planner": payload["indepth_planner"]}

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
