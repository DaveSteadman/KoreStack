# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Internal guardrail/smoke test suite for KoreAgent core modules.
#
# Uses unittest.TestCase to validate key module imports and basic function behaviour:
#   - skill_executor.execute_tool_call dispatch
#   - scratchpad read/write round-trip
#   - file_access skill validation
#   - web tools availability
#   - orchestration helpers (compact_context, assess_compact)
#
# Run manually via:
#   python -m unittest testing.unit.test_guardrail_smoke
#   python -m pytest testing/test_guardrail_smoke.py -v
#
# The /test slash flow runs prompt suites through testing/test_wrapper.py and then
# executes a focused guardrail smoke subset from this file as a post-check.
#
# Related modules:
#   - testing/test_wrapper.py  -- wraps individual test files for /test execution
#   - skill_executor.py        -- execute_tool_call
#   - scratchpad.py            -- scratchpad_save, scratchpad_load
# ====================================================================================================
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
CODE_DIR  = Path(__file__).resolve().parents[2]

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import datasets_pkg as datasets_module
from agent.tool_runtime import loop as tool_loop_module
from agent.orchestration import engine as orchestration_module
from sessions import tool_selection as tool_selection_state_module
from agent.orchestration import planning as task_planning_module
from conversation_state import decode_background_context
from conversation_state import encode_background_context
from skill_executor import execute_tool_call
from datasets_pkg import store as datasets_store
import mcp_client
from agent.orchestration.engine import ConversationHistory
from agent.orchestration.engine import OrchestratorConfig
from agent.orchestration.engine import _filter_workflow_tools
from agent.orchestration.engine import orchestrate_prompt
from input_layer import koreconv_input as koreconv_input_module
from indepth_planner_store import should_bootstrap_workflow
from system_skills.Workflow import workflow_skill as workflow_skill_module
from datasets_pkg import auto_route_tool_result
from datasets_pkg import clear_session_datasets
from datasets_pkg import dataset_drop_where
from datasets_pkg import dataset_expand_full_text
from datasets_pkg import dataset_filter
from datasets_pkg import dataset_get
from datasets_pkg import dataset_inspect
from datasets_pkg import dataset_list
from datasets_pkg import dataset_rename
from datasets_pkg import dataset_save
from datasets_pkg import dataset_write_koredoc
from datasets_pkg import delete_session_datasets
from datasets_pkg import get_persisted_datasets_payload
from datasets_pkg import restore_persisted_datasets
from prompt_builder import build_system_message
from scratchpad import scratchpad_clear
from scratchpad import get_store
from scratchpad import scratchpad_load
from scratchpad import scratchpad_list
from scratchpad import scratchpad_query
from scratchpad import scratchpad_save
from sessions.runtime import get_active_session_id
from sessions.runtime import bind_session
from skills_catalog_builder import build_tool_definitions
from skills_catalog_builder import load_skills_payload
from system_skills.FileAccess import file_access_skill as file_access_module
from system_skills.ToolSelection import tool_selection_skill as tool_selection_skill_module
from system_skills.FileAccess.file_access_skill import file_write
from system_skills.FileAccess.file_access_skill import file_read
from system_skills.FileAccess.file_access_skill import folder_create
from KoreLiveWeb.app.web_fetch    import fetch_page_text
from KoreLiveWeb.app.web_search   import search_web
from skills.SystemInfo.system_info_skill import get_system_info_string
from KoreDocs.app import korefile as koredocs_korefile
from KoreCommon import datauser_fs as datauser_fs_module
from agent.tool_runtime.loop import normalize_tool_request
from agent.tool_runtime.loop import _derive_auto_scratchpad_key
from agent.tool_runtime.loop import _extract_graph_connection_batch_from_text
from tool_result import ToolCallResult
import api.app as api_module
from input_layer import slash_commands as slash_commands_module
from input_layer import slash_command_handlers_sessions as session_handlers_module
from input_layer.routes_sessions import _queue_timeout_for_prompt
from input_layer.routes_sessions import _runtime_config_for_prompt
from input_layer.slash_command_handlers_testing import _result_counts
from testing.system import runner as test_wrapper_module
from testing.unit.guardrail_support import load_test_skills_payload
from testing.unit.guardrail_support import reset_guardrail_state
from utils import workspace_utils as workspace_utils_module
from utils.workspace_utils import get_user_data_dir


class GuardrailSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skills_payload = load_test_skills_payload(CODE_DIR)
        reset_guardrail_state()

    def tearDown(self) -> None:
        reset_guardrail_state()

    def test_ordinary_prompt_hides_persistent_plan_functions(self) -> None:
        payload = {
            "skills": [
                {
                    "skill_name": "Workflow",
                    "functions": ["workflow_create()", "workflow_get_summary()", "workflow_import(name: str)"],
                    "param_descriptions": {"workflow_create": {}, "workflow_get_summary": {}, "workflow_import": {"name": "Archive name."}},
                },
                {
                    "skill_name": "Files",
                    "functions": ["file_read(path: str)"],
                    "param_descriptions": {"file_read": {"path": "Path to read."}},
                },
            ]
        }

        filtered = _filter_workflow_tools(payload, enabled=False, has_plan=False)

        self.assertEqual(len(filtered["skills"]), 1)
        self.assertEqual(filtered["skills"][0]["functions"], ["file_read(path: str)"])
        self.assertNotIn("workflow_create", filtered["skills"][0]["param_descriptions"])

        creation_only = _filter_workflow_tools(payload, enabled=True, has_plan=False)
        workflow_skill = next(skill for skill in creation_only["skills"] if skill["skill_name"] == "Workflow")
        self.assertEqual(workflow_skill["functions"], ["workflow_create()", "workflow_import(name: str)"])

    def test_empty_korechat_rejects_existing_plan_operations(self) -> None:
        with patch.object(workflow_skill_module, "get_simple_plan", return_value={}):
            response = workflow_skill_module.workflow_get_summary()

        self.assertIn("Use workflow_create or workflow_import first", response)

    def test_lightweight_plan_workflow_does_not_bootstrap_a_persistent_plan(self) -> None:
        lightweight_plan = {"workflow": ["inspect", "plan", "act", "validate", "complete"]}

        self.assertFalse(
            should_bootstrap_workflow(
                "search KoreData reference for machine learning",
                lightweight_plan,
            )
        )
        self.assertTrue(should_bootstrap_workflow("create a Workflow for the migration"))

    def test_task_plan_activates_current_and_next_phase_tools(self) -> None:
        plan = task_planning_module.validate_task_plan(
            {
                "objective": "Inspect, edit, and verify a source file.",
                "current_phase": "inspect",
                "workflow": ["inspect", "act", "validate", "complete"],
                "phase_tools": ["file_read"],
                "phase_tool_map": {
                    "inspect": ["file_read"],
                    "act":     ["file_write"],
                    "validate": ["file_read"],
                },
            },
            known_tool_names={"file_read", "file_write"},
        )

        self.assertEqual(plan.phase_tools, ["file_read"])
        self.assertEqual(plan.activation_tools(), ["file_read", "file_write"])

    def test_workflow_task_context_informs_lightweight_tool_selection(self) -> None:
        _prompt, trace = task_planning_module.build_planning_prompt(
            user_prompt        = "run task 1",
            planning_context   = json.dumps({"static_instruction": {"instruction": "Search KoreData and write sources.txt."}}),
            capability_catalog = [
                {"name": "koredata_search", "description": "Search KoreData references.", "active": False},
                {"name": "file_write", "description": "Write a file.", "active": False},
            ],
        )

        self.assertEqual(trace["selected_count"], 2)
        self.assertEqual(trace["tokens"], [])

    def test_task_plan_preserves_multiple_typed_outputs_per_step(self) -> None:
        plan = task_planning_module.validate_task_plan(
            {
                "objective": "Research and write outputs.",
                "current_phase": "act",
                "workflow": ["act", "validate", "complete"],
                "phase_tools": ["file_write"],
                "steps": [
                    {
                        "id": "write_outputs",
                        "phase": "act",
                        "action": "Write the requested research outputs.",
                        "tools": ["file_write"],
                        "outputs": [
                            {"type": "file", "path": "plan_002/sources.txt", "minimum_bytes": 1},
                            {"type": "file", "path": "plan_002/search_criteria.txt", "minimum_bytes": 1},
                            {"type": "dataset", "name": "research_sources"},
                        ],
                        "completion_checks": ["All declared files exist."],
                    }
                ],
            },
            known_tool_names={"file_write"},
        )

        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(len(plan.steps[0]["outputs"]), 3)
        self.assertEqual(plan.steps[0]["outputs"][0]["target"], "plan_002/sources.txt")
        self.assertEqual(plan.steps[0]["outputs"][2]["type"], "dataset")

    def test_koredata_search_plan_includes_dataset_reading_tools_in_its_phase(self) -> None:
        plan = task_planning_module.validate_task_plan(
            {
                "objective": "Search KoreData and write a source list.",
                "current_phase": "act",
                "workflow": ["act", "complete"],
                "phase_tools": ["koredata_search", "file_write"],
                "phase_tool_map": {"act": ["koredata_search", "file_write"]},
                "steps": [
                    {
                        "id": "search",
                        "phase": "act",
                        "action": "Search KoreData, then turn the returned records into a source list.",
                        "tools": ["koredata_search", "file_write"],
                        "outputs": [{"type": "file", "target": "sources.txt"}],
                    }
                ],
            },
            known_tool_names={"koredata_search", "dataset_get", "dataset_inspect", "file_write"},
        )

        self.assertIn("dataset_get", plan.phase_tool_map["act"])
        self.assertIn("dataset_inspect", plan.phase_tool_map["act"])
        self.assertIn("dataset_get", plan.steps[0]["tools"])
        self.assertIn("dataset_inspect", plan.activation_tools())

    def test_task_plan_completion_gate_reports_missing_declared_file(self) -> None:
        with bind_session("task_plan_completion_gate"):
            plan = task_planning_module.validate_task_plan(
                {
                    "objective": "Write the requested output.",
                    "current_phase": "act",
                    "workflow": ["act", "validate", "complete"],
                    "phase_tools": ["file_write"],
                    "steps": [
                        {
                            "id": "write_output",
                            "phase": "act",
                            "action": "Write the requested output.",
                            "tools": ["file_write"],
                            "outputs": [{"type": "file", "target": "__missing_task_plan_output__.txt", "minimum_bytes": 1}],
                            "completion_checks": ["The file exists."],
                        }
                    ],
                },
                known_tool_names={"file_write"},
            )
            task_planning_module.persist_task_plan(plan)
            gaps = task_planning_module.get_task_plan_completion_gaps()

        self.assertTrue(any("__missing_task_plan_output__.txt" in gap for gap in gaps))

    def test_workflow_task_run_ignores_planner_invented_final_output_paths(self) -> None:
        with bind_session("workflow_task_contract_completion"):
            plan = task_planning_module.validate_task_plan(
                {
                    "objective": "Write the durable report output.",
                    "current_phase": "act",
                    "workflow": ["act", "complete"],
                    "steps": [
                        {
                            "id": "temporary_work",
                            "phase": "act",
                            "action": "Prepare temporary work.",
                            "tools": [],
                            "outputs": [{"type": "file", "target": "wrong-folder/report.md", "minimum_bytes": 1}],
                        }
                    ],
                },
                known_tool_names=set(),
            )
            task_planning_module.persist_task_plan(plan)

            self.assertTrue(task_planning_module.get_task_plan_completion_gaps())
            self.assertEqual(task_planning_module.get_task_plan_completion_gaps(include_declared_outputs=False), [])

    def test_expected_skill_failure_counts_as_an_attempted_task_step(self) -> None:
        with bind_session("task_plan_expected_file_error"):
            plan = task_planning_module.validate_task_plan(
                {
                    "objective": "Attempt to read a missing file and report the outcome.",
                    "current_phase": "act",
                    "workflow": ["act", "complete"],
                    "steps": [
                        {
                            "id": "attempt_read",
                            "phase": "act",
                            "action": "Attempt the requested read.",
                            "tools": ["file_read"],
                            "outputs": [],
                        }
                    ],
                },
                known_tool_names={"file_read"},
            )
            task_planning_module.persist_task_plan(plan)
            task_planning_module.advance_task_plan_phase(
                [
                    ToolCallResult(
                        tool="file_read",
                        function="file_read",
                        module="FileAccess",
                        arguments={"path": "missing.txt"},
                        result="File not found: datauser/missing.txt",
                        status="error",
                        error="File not found: datauser/missing.txt",
                    )
                ]
            )

            self.assertEqual(task_planning_module.get_task_plan_completion_gaps(), [])

    def test_plan_guard_rejection_does_not_count_as_an_attempted_task_step(self) -> None:
        with bind_session("task_plan_guard_is_not_attempt"):
            plan = task_planning_module.validate_task_plan(
                {
                    "objective": "Read a file.",
                    "current_phase": "act",
                    "workflow": ["act", "complete"],
                    "steps": [{"id": "read", "phase": "act", "action": "Read.", "tools": ["file_read"]}],
                },
                known_tool_names={"file_read"},
            )
            task_planning_module.persist_task_plan(plan)
            task_planning_module.advance_task_plan_phase(
                [
                    ToolCallResult(
                        tool="file_read",
                        function="file_read",
                        module="",
                        arguments={"path": "x.txt"},
                        result="[PLAN_GUARD] Tool 'file_read' is not available in the current task phase.",
                        status="error",
                        error="tool is outside the active task-plan phase",
                    )
                ]
            )

            self.assertTrue(any("Step 'read'" in gap for gap in task_planning_module.get_task_plan_completion_gaps()))

    def test_task_plan_repairs_invalid_tool_names_with_a_second_planner_call(self) -> None:
        responses = iter(
            [
                SimpleNamespace(response='{"objective":"calculate","phase_tools":["python"],"current_phase":"act"}'),
                SimpleNamespace(response='{"objective":"calculate","phase_tools":["python_execute"],"current_phase":"act"}'),
            ]
        )

        plan = task_planning_module.create_task_plan(
            user_prompt        = "Calculate a result.",
            capability_catalog = [{"name": "python_execute", "description": "Run Python."}],
            known_tool_names   = {"python_execute"},
            call_llm_chat      = lambda **_kwargs: next(responses),
            model_name         = "test-model",
            num_ctx            = 4096,
        )

        self.assertEqual(plan.phase_tools, ["python_execute"])

    def test_task_plan_is_mirrored_to_the_named_scratchpad(self) -> None:
        with bind_session("named_task_plan"):
            scratchpad_clear()
            task_planning_module.persist_task_plan(
                task_planning_module.fallback_task_plan(user_prompt="Inspect the workspace.", reason="test")
            )
            task_planning_module.record_task_plan_event("inspected", "planning.py")

            payload = json.loads(scratchpad_load("task_plan"))

            self.assertIn("task_plan", scratchpad_list())
            self.assertIn("task_plan", get_store())
            self.assertEqual(payload["objective"], "Inspect the workspace.")
            self.assertEqual(payload["state"]["events"][-1]["type"], "inspected")

    def test_task_plan_advances_phase_and_refreshes_activation_tools(self) -> None:
        with bind_session("task_plan_phase_flow"):
            plan = task_planning_module.validate_task_plan(
                {
                    "objective": "Inspect then edit then validate.",
                    "current_phase": "inspect",
                    "workflow": ["inspect", "act", "validate", "complete"],
                    "phase_tools": ["file_read"],
                    "phase_tool_map": {
                        "inspect": ["file_read"],
                        "act": ["file_write"],
                        "validate": ["file_read"],
                    },
                },
                known_tool_names={"file_read", "file_write", "tools_catalog_list", "tools_active_add"},
            )
            task_planning_module.persist_task_plan(plan)

            self.assertEqual(task_planning_module.get_task_plan_phase(), "inspect")
            before_tools = set(task_planning_module.get_task_plan_activation_tools())
            self.assertIn("file_read", before_tools)
            self.assertIn("file_write", before_tools)

            task_planning_module.advance_task_plan_phase(
                [
                    ToolCallResult(
                        tool="file_read",
                        function="file_read",
                        module="file_access",
                        arguments={"path": "x"},
                        result="ok",
                    )
                ]
            )

            self.assertEqual(task_planning_module.get_task_plan_phase(), "act")
            after_tools = set(task_planning_module.get_task_plan_activation_tools())
            self.assertIn("file_write", after_tools)
            self.assertIn("tools_catalog_list", after_tools)
            self.assertIn("tools_active_add", after_tools)
            self.assertNotIn("delegate", after_tools)

    def test_task_plan_holds_phase_when_phase_specific_criteria_not_met(self) -> None:
        with bind_session("task_plan_phase_hold"):
            plan = task_planning_module.validate_task_plan(
                {
                    "objective": "Inspect then edit.",
                    "current_phase": "inspect",
                    "workflow": ["inspect", "act", "complete"],
                    "phase_tools": ["file_read"],
                    "phase_tool_map": {
                        "inspect": ["file_read"],
                        "act": ["file_write"],
                    },
                },
                known_tool_names={"file_read", "file_write"},
            )
            task_planning_module.persist_task_plan(plan)

            task_planning_module.advance_task_plan_phase(
                [
                    ToolCallResult(
                        tool="file_write",
                        function="file_write",
                        module="file_access",
                        arguments={"path": "x", "content": "y"},
                        result="ok",
                    )
                ]
            )

            self.assertEqual(task_planning_module.get_task_plan_phase(), "inspect")

    def test_task_plan_advances_plan_phase_after_its_declared_tool_runs(self) -> None:
        with bind_session("task_plan_plan_phase_flow"):
            plan = task_planning_module.validate_task_plan(
                {
                    "objective": "Search, then write a report.",
                    "current_phase": "plan",
                    "workflow": ["plan", "act", "validate", "complete"],
                    "phase_tools": ["koredata_search"],
                    "phase_tool_map": {
                        "plan": ["koredata_search"],
                        "act": ["file_write"],
                        "validate": ["file_read"],
                    },
                    "steps": [{"phase": "plan", "tools": ["koredata_search"]}],
                },
                known_tool_names={"koredata_search", "file_write", "file_read"},
            )
            task_planning_module.persist_task_plan(plan)

            task_planning_module.advance_task_plan_phase(
                [
                    ToolCallResult(
                        tool="koredata_search",
                        function="koredata_search",
                        module="KoreData",
                        arguments={"query": "evidence"},
                        result="ok",
                    )
                ]
            )

            self.assertEqual(task_planning_module.get_task_plan_phase(), "act")

    def test_task_plan_records_planner_selection_trace(self) -> None:
        with bind_session("task_plan_selection_trace"):
            responses = iter(
                [
                    SimpleNamespace(
                        response='{"objective":"inspect and update a file","current_phase":"inspect","phase_tools":["file_read"],"workflow":["inspect","act","complete"],"phase_tool_map":{"inspect":["file_read"],"act":["file_write"]}}'
                    )
                ]
            )
            capability_catalog = [
                {
                    "name": "file_read",
                    "description": "Read a file from data storage.",
                    "active": True,
                    "origin": "local",
                    "skill_name": "FileAccess",
                    "triggers": ["read file"],
                    "param_names": ["path", "max_chars"],
                },
                {
                    "name": "file_write",
                    "description": "Write text content into a file.",
                    "active": False,
                    "origin": "local",
                    "skill_name": "FileAccess",
                    "triggers": ["write file"],
                    "param_names": ["path", "content"],
                },
            ]

            plan = task_planning_module.create_task_plan(
                user_prompt="Read config then update output file.",
                capability_catalog=capability_catalog,
                known_tool_names={"file_read", "file_write"},
                call_llm_chat=lambda **_kwargs: next(responses),
                model_name="test-model",
                num_ctx=4096,
            )
            task_planning_module.persist_task_plan(plan)

            trace = task_planning_module.get_last_planner_selection_trace()
            self.assertEqual(trace.get("fallback_all"), False)
            self.assertGreaterEqual(int(trace.get("selected_count", 0)), 1)
            self.assertGreaterEqual(int(trace.get("total_catalog", 0)), 2)
            self.assertIsInstance(trace.get("top"), list)

    def test_orchestrate_prompt_phase_enforcement_progresses_without_deadlock(self) -> None:
        class _DummyLogger:
            def log(self, _message: str = "") -> None:
                pass

            def log_file_only(self, _message: str = "") -> None:
                pass

            def log_section(self, _title: str) -> None:
                pass

            def log_section_file_only(self, _title: str) -> None:
                pass

        class _FakeResult:
            def __init__(self, response: str, tool_calls: list | None = None) -> None:
                self.response = response
                self.message = {"content": response}
                self.finish_reason = "tool_calls" if tool_calls else "stop"
                self.prompt_tokens = 10
                self.completion_tokens = 5
                self.tokens_per_second = 1.0
                self.tool_calls = tool_calls or []

        def _tool_call(name: str, arguments: dict | None = None) -> dict:
            return {
                "id": f"tc_{name}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments or {}),
                },
            }

        planner_response = _FakeResult(
            '{"objective":"Inspect then edit then validate a file","task_class":"coding","confidence":0.8,"current_phase":"inspect","workflow":["inspect","act","validate","complete"],"phase_tools":["file_read"],"phase_tool_map":{"inspect":["file_read"],"act":["file_write"],"validate":["file_read"]},"required_artifacts":["source evidence"],"validation_requirements":["read after write"],"completion_contract":"state changes and validation evidence","rationale":"perform bounded inspect-edit-validate"}'
        )
        loop_responses = [
            _FakeResult("", [_tool_call("file_read", {"path": "notes.txt"})]),
            _FakeResult("", [_tool_call("file_write", {"path": "notes.txt", "content": "updated"})]),
            _FakeResult("", [_tool_call("file_read", {"path": "notes.txt"})]),
            _FakeResult("Completed."),
        ]
        responses = [planner_response, *loop_responses]
        calls: list[str] = []

        def fake_call_llm_chat(**_kwargs):
            return responses.pop(0)

        def fake_execute_tool_call(func_name, arguments, *_args):
            calls.append(func_name)
            return ToolCallResult(
                tool=func_name,
                function=func_name,
                module="file_access",
                arguments=arguments,
                result="ok",
            )

        skills_payload = {
            "skills": [
                {
                    "skill_name": "FileAccess",
                    "module": "system_skills/FileAccess/file_access_skill.py",
                    "functions": [
                        "file_read(path: str, max_chars: int = 8000)",
                        "file_write(path: str, content: str, skip_content_guard: bool = False)",
                    ],
                    "purpose": "Read/write files.",
                    "triggers": ["read", "write"],
                    "param_descriptions": {
                        "file_read": {"path": "Path", "max_chars": "Limit"},
                        "file_write": {"path": "Path", "content": "Content", "skip_content_guard": "Guard"},
                    },
                    "origin": "local",
                    "availability": "configured",
                    "role": "optional",
                    "trust_boundary": "internal",
                }
            ]
        }
        config = OrchestratorConfig(
            resolved_model="test-model",
            num_ctx=8192,
            max_iterations=6,
            skills_payload=skills_payload,
            task_planning_enabled=True,
            task_plan_enforce_phase=True,
        )

        with (
            bind_session("phase_enforcement_e2e"),
            patch.object(orchestration_module, "call_llm_chat", side_effect=fake_call_llm_chat),
            patch.object(tool_loop_module, "execute_tool_call", side_effect=fake_execute_tool_call),
        ):
            final_response, _prompt_tokens, _completion_tokens, run_success, _tps = orchestrate_prompt(
                user_prompt="Update notes.txt after inspecting it and validate the result.",
                config=config,
                logger=_DummyLogger(),
                conversation_history=None,
                session_context=None,
                quiet=True,
                bound_session_id="phase_enforcement_e2e",
            )

            self.assertTrue(run_success)
            self.assertEqual(final_response, "Completed.")
            self.assertEqual(calls, ["file_read", "file_write", "file_read"])
            self.assertEqual(task_planning_module.get_task_plan_phase(), "complete")

    def test_tool_loop_auto_activates_known_inactive_tool_and_blocks_dead_end_final(self) -> None:
        class _DummyLogger:
            def log(self, _message: str = "") -> None:
                pass

            def log_file_only(self, _message: str = "") -> None:
                pass

            def log_section(self, _title: str) -> None:
                pass

            def log_section_file_only(self, _title: str) -> None:
                pass

        class _FakeResult:
            def __init__(self, response: str, tool_calls: list | None = None) -> None:
                self.response = response
                self.message = {"content": response}
                self.finish_reason = "tool_calls" if tool_calls else "stop"
                self.prompt_tokens = 10
                self.completion_tokens = 5
                self.tokens_per_second = 1.0
                self.tool_calls = tool_calls or []

        def _tool_call(name: str) -> dict:
            return {
                "id": f"tc_{name}",
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }

        responses = [
            _FakeResult("", [_tool_call("dataset_list")]),
            _FakeResult("I should inspect the active tool set first."),
            _FakeResult("", [_tool_call("dataset_list")]),
            _FakeResult("Datasets listed."),
        ]
        runtime_state = {
            "active": {"tools_catalog_list", "tools_active_add"},
            "known": {"dataset_list", "tools_catalog_list", "tools_active_add"},
        }
        calls: list[str] = []

        def fake_call_llm_chat(**_kwargs):
            return responses.pop(0)

        def fake_execute_tool_call(func_name, arguments, *_args):
            calls.append(func_name)
            if func_name == "dataset_list" and func_name not in runtime_state["active"]:
                raise RuntimeError("Tool 'dataset_list' is not active for this conversation")
            return ToolCallResult(
                tool=func_name,
                function=func_name,
                module="datasets",
                arguments=arguments,
                result="No datasets stored.",
            )

        def fake_promote_selected_tools(tool_names, *args, **kwargs):
            for tool_name in tool_names:
                runtime_state["active"].add(tool_name)
            return {
                "added": list(tool_names),
                "promoted": [],
                "evicted": [],
                "active_tools": sorted(runtime_state["active"]),
            }

        def fake_runtime_provider():
            return {
                "tool_defs": [],
                "catalog_gates": {},
                "active_tool_names": set(runtime_state["active"]),
                "missing_selected": [],
                "all_known_tool_names": set(runtime_state["known"]),
            }

        config = SimpleNamespace(
            resolved_model="test-model",
            max_iterations=4,
            num_ctx=8192,
            skills_payload={"skills": []},
        )
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "list the datasets"},
        ]
        context_map = [
            {"round": 0, "role": "sys", "label": "system", "chars": 6, "auto_key": None, "msg_idx": 0},
            {"round": 0, "role": "user", "label": "prompt", "chars": 17, "auto_key": None, "msg_idx": 1},
        ]

        with (
            patch.object(tool_loop_module, "execute_tool_call", side_effect=fake_execute_tool_call),
            patch.object(tool_selection_state_module, "promote_selected_tools", side_effect=fake_promote_selected_tools),
        ):
            final_response, _prompt_tokens, _completion_tokens, run_success, _tps, _tool_outputs = tool_loop_module.run_tool_loop(
                config=config,
                messages=messages,
                tool_defs=[],
                catalog_gates={},
                active_tool_names=set(runtime_state["active"]),
                context_map=context_map,
                user_prompt="list the datasets",
                logger=_DummyLogger(),
                quiet=True,
                call_llm_chat=fake_call_llm_chat,
                stop_requested=lambda: False,
                clear_stop=lambda: None,
                tool_runtime_provider=fake_runtime_provider,
            )

        self.assertTrue(run_success)
        self.assertEqual(final_response, "Datasets listed.")
        self.assertEqual(calls, ["dataset_list", "dataset_list"])
        self.assertIn("dataset_list", runtime_state["active"])
        joined_messages = "\n".join(str(message.get("content", "")) for message in messages)
        self.assertIn("It has been added to the active tool set", joined_messages)
        self.assertIn("Recovery still required: do not answer yet. Retry `dataset_list` now", joined_messages)

    def test_plan_run_to_completion_blocks_final_answer_until_remaining_task_is_ran(self) -> None:
        class _DummyLogger:
            def log(self, _message: str = "") -> None:
                pass

            def log_file_only(self, _message: str = "") -> None:
                pass

            def log_section(self, _title: str) -> None:
                pass

            def log_section_file_only(self, _title: str) -> None:
                pass

        class _FakeResult:
            def __init__(self, response: str, tool_calls: list | None = None) -> None:
                self.response           = response
                self.message            = {"content": response}
                self.prompt_tokens      = 10
                self.completion_tokens  = 5
                self.tokens_per_second  = 1.0
                self.tool_calls         = tool_calls or []

        def _tool_call(name: str, arguments: str = "{}") -> dict:
            return {
                "id":       f"tc_{name}",
                "type":     "function",
                "function": {"name": name, "arguments": arguments},
            }

        remaining = [{"id": "2", "static": {"instruction": "Collect the evidence."}, "dynamic": {"ran": False}}]
        responses = [
            _FakeResult("", [_tool_call("workflow_run_to_completion")]),
            _FakeResult("The plan is complete."),
            _FakeResult("", [_tool_call("workflow_mark_task_ran", '{"task_id":"2"}')]),
            _FakeResult("The plan is complete."),
        ]
        calls: list[str] = []

        def fake_call_llm_chat(**_kwargs):
            return responses.pop(0)

        def fake_execute_tool_call(func_name, arguments, *_args):
            calls.append(func_name)
            if func_name == "workflow_mark_task_ran":
                remaining.clear()
            return ToolCallResult(
                tool      = func_name,
                function  = func_name,
                module    = "Workflow",
                arguments = arguments,
                result    = "ok",
            )

        config = SimpleNamespace(resolved_model="test-model", max_iterations=5, num_ctx=8192, skills_payload={"skills": []})
        messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "run the plan"}]
        context_map = [
            {"round": 0, "role": "sys",  "label": "system", "chars": 6,  "auto_key": None, "msg_idx": 0},
            {"round": 0, "role": "user", "label": "prompt", "chars": 12, "auto_key": None, "msg_idx": 1},
        ]

        with patch.object(tool_loop_module, "execute_tool_call", side_effect=fake_execute_tool_call):
            final_response, _prompt_tokens, _completion_tokens, run_success, _tps, _outputs = tool_loop_module.run_tool_loop(
                config                             = config,
                messages                           = messages,
                tool_defs                          = [],
                catalog_gates                      = {},
                context_map                        = context_map,
                user_prompt                        = "run the plan",
                logger                             = _DummyLogger(),
                quiet                              = True,
                call_llm_chat                      = fake_call_llm_chat,
                stop_requested                     = lambda: False,
                clear_stop                         = lambda: None,
                run_to_completion_remaining_provider = lambda: remaining,
            )

        self.assertTrue(run_success)
        self.assertEqual(final_response, "The plan is complete.")
        self.assertEqual(calls, ["workflow_run_to_completion", "workflow_mark_task_ran"])
        self.assertIn("Task 2 has not been run", "\n".join(str(item.get("content", "")) for item in messages))

    def test_tool_loop_suggests_corrected_tool_name_for_invalid_request(self) -> None:
        class _DummyLogger:
            def log(self, _message: str = "") -> None:
                pass

            def log_file_only(self, _message: str = "") -> None:
                pass

            def log_section(self, _title: str) -> None:
                pass

            def log_section_file_only(self, _title: str) -> None:
                pass

        class _FakeResult:
            def __init__(self, response: str, tool_calls: list | None = None) -> None:
                self.response = response
                self.message = {"content": response}
                self.finish_reason = "tool_calls" if tool_calls else "stop"
                self.prompt_tokens = 10
                self.completion_tokens = 5
                self.tokens_per_second = 1.0
                self.tool_calls = tool_calls or []

        def _tool_call(name: str) -> dict:
            return {
                "id": f"tc_{name}",
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }

        responses = [
            _FakeResult("", [_tool_call("koredec_table_read")]),
            _FakeResult("", [_tool_call("koredoc_table_read")]),
            _FakeResult("Read completed."),
        ]
        runtime_state = {
            "active": {"koredoc_table_read", "tools_catalog_list", "tools_active_add"},
            "known": {"koredoc_table_read", "tools_catalog_list", "tools_active_add"},
        }
        calls: list[str] = []

        def fake_call_llm_chat(**_kwargs):
            return responses.pop(0)

        def fake_execute_tool_call(func_name, arguments, *_args):
            calls.append(func_name)
            if func_name == "koredec_table_read":
                raise RuntimeError("Tool 'koredec_table_read' not found in skills catalog")
            return ToolCallResult(
                tool=func_name,
                function=func_name,
                module="docs",
                arguments=arguments,
                result="table data",
            )

        def fake_runtime_provider():
            return {
                "tool_defs": [],
                "catalog_gates": {},
                "active_tool_names": set(runtime_state["active"]),
                "missing_selected": [],
                "all_known_tool_names": set(runtime_state["known"]),
            }

        config = SimpleNamespace(
            resolved_model="test-model",
            max_iterations=4,
            num_ctx=8192,
            skills_payload={"skills": []},
        )
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "read the table"},
        ]
        context_map = [
            {"round": 0, "role": "sys", "label": "system", "chars": 6, "auto_key": None, "msg_idx": 0},
            {"round": 0, "role": "user", "label": "prompt", "chars": 14, "auto_key": None, "msg_idx": 1},
        ]

        with patch.object(tool_loop_module, "execute_tool_call", side_effect=fake_execute_tool_call):
            final_response, _prompt_tokens, _completion_tokens, run_success, _tps, _tool_outputs = tool_loop_module.run_tool_loop(
                config=config,
                messages=messages,
                tool_defs=[],
                catalog_gates={},
                active_tool_names=set(runtime_state["active"]),
                context_map=context_map,
                user_prompt="read the table",
                logger=_DummyLogger(),
                quiet=True,
                call_llm_chat=fake_call_llm_chat,
                stop_requested=lambda: False,
                clear_stop=lambda: None,
                tool_runtime_provider=fake_runtime_provider,
            )

        self.assertTrue(run_success)
        self.assertEqual(final_response, "Read completed.")
        self.assertEqual(calls, ["koredec_table_read", "koredoc_table_read"])
        joined_messages = "\n".join(str(message.get("content", "")) for message in messages)
        self.assertIn("Closest valid tool: `koredoc_table_read`.", joined_messages)
        self.assertIn("Retry using `koredoc_table_read` only.", joined_messages)

    def test_test_wrapper_fails_single_prompt_on_no_results_output(self) -> None:
        passed, reason = test_wrapper_module._single_item_pass_status(
            exit_code=0,
            final_output="No results were found for this query.",
            log_file="",
        )
        self.assertFalse(passed)
        self.assertEqual(reason, "Search returned no results")

    def test_test_wrapper_fails_exchange_on_search_failure_output(self) -> None:
        passed, reason = test_wrapper_module._exchange_pass_status(
            exit_code=0,
            turn_outputs={1: "Search failed: HTTP 429", 2: "fallback"},
            any_assert_fail=False,
            log_file="",
        )
        self.assertFalse(passed)
        self.assertEqual(reason, "Search returned no results")

    def test_queue_timeout_for_prompt_disables_scheduler_timeout_only_for_test(self) -> None:
        self.assertEqual(_queue_timeout_for_prompt("/systemtest all"), 0)
        self.assertEqual(_queue_timeout_for_prompt("   /systemtest smoke   "), 0)
        self.assertIsNone(_queue_timeout_for_prompt("/test all"))
        self.assertIsNone(_queue_timeout_for_prompt("   /test smoke   "))
        self.assertIsNone(_queue_timeout_for_prompt("/testtrend smoke"))
        self.assertIsNone(_queue_timeout_for_prompt("/systemtesttrend smoke"))
        self.assertIsNone(_queue_timeout_for_prompt("normal prompt"))
        self.assertIsNone(_queue_timeout_for_prompt(""))

    def test_slash_command_outputs_use_ascii_arrows(self) -> None:
        outputs: list[str] = []
        ctx = SimpleNamespace(
            config=SimpleNamespace(num_ctx=4096, max_iterations=4, resolved_model="test-model"),
            output=lambda text, level="info": outputs.append(text),
        )

        with patch.object(slash_commands_module, "register_session_config"):
            slash_commands_module._cmd_ctx("size 10000", ctx)
        slash_commands_module._cmd_rounds("6", ctx)
        with patch.object(slash_commands_module, "get_llm_timeout", return_value=30):
            with patch.object(slash_commands_module, "set_llm_timeout"):
                slash_commands_module._cmd_timeout("60", ctx)

        joined = "\n".join(outputs)
        self.assertIn("->", joined)
        self.assertNotIn("\u2192", joined)


if __name__ == "__main__":
    unittest.main()
