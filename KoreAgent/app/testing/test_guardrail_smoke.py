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
#   python -m unittest testing.test_guardrail_smoke
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


REPO_ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = Path(__file__).resolve().parents[1]

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import datasets_pkg as datasets_module
from agent.tool_runtime import loop as tool_loop_module
from sessions import tool_selection as tool_selection_state_module
from agent.orchestration import planning as task_planning_module
from conversation_state import decode_background_context
from conversation_state import encode_background_context
from skill_executor import execute_tool_call
from datasets_pkg import store as datasets_store
import mcp_client
from agent.orchestration.engine import ConversationHistory
from agent.orchestration.engine import _delegate_tls
from agent.orchestration.engine import delegate_subrun
from agent.orchestration.engine import OrchestratorConfig
from agent.orchestration.engine import orchestrate_prompt
from input_layer import koreconv_input as koreconv_input_module
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
from system_skills.Delegate import delegate_runtime as delegate_runtime_module
from system_skills.Delegate import delegate_skill   as delegate_skill_module
from system_skills.FileAccess import file_access_skill as file_access_module
from system_skills.ToolSelection import tool_selection_skill as tool_selection_skill_module
from system_skills.FileAccess.file_access_skill import file_write
from system_skills.FileAccess.file_access_skill import file_read
from system_skills.FileAccess.file_access_skill import folder_create
from KoreLiveWeb.app.web_fetch    import fetch_page_text
from KoreLiveWeb.app.web_search   import search_web
from KoreLiveWeb.app.web_research import research_traverse
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
from testing import test_wrapper as test_wrapper_module
from testing.guardrail_support import load_test_skills_payload
from testing.guardrail_support import reset_guardrail_state
from utils import workspace_utils as workspace_utils_module
from utils.workspace_utils import get_user_data_dir


class GuardrailSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skills_payload = load_test_skills_payload(CODE_DIR)
        reset_guardrail_state()

    def tearDown(self) -> None:
        reset_guardrail_state()

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

    def test_task_plan_is_not_exposed_through_the_scratchpad(self) -> None:
        with bind_session("private_task_plan"):
            task_planning_module.persist_task_plan(
                task_planning_module.fallback_task_plan(user_prompt="Inspect the workspace.", reason="test")
            )
            scratchpad_save("__internal_test", "controller state")

            self.assertEqual(scratchpad_list(), "Scratchpad is empty.")
            self.assertNotIn("__internal_test", get_store())

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
                known_tool_names={"file_read", "file_write", "tools_catalog_list", "tools_active_add", "delegate"},
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
            self.assertIn("delegate", after_tools)

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
            patch("orchestration.call_llm_chat", side_effect=fake_call_llm_chat),
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

    def test_test_wrapper_extracts_delegate2_log_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "delegate.log"
            log_path.write_text(
                "[delegate2] queued task_id=dlg_20260712_220145_569ff5b3 child_session_id=delegate_task_dlg_20260712_220145_569ff5b3\n",
                encoding="utf-8",
            )

            events = test_wrapper_module._extract_delegate_events(str(log_path))

        self.assertEqual(len(events), 1)
        self.assertIn("[delegate2] queued", events[0])

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
            patch("tool_selection_state.promote_selected_tools", side_effect=fake_promote_selected_tools),
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
        self.assertEqual(_queue_timeout_for_prompt("/test all"), 0)
        self.assertEqual(_queue_timeout_for_prompt("   /test smoke   "), 0)
        self.assertIsNone(_queue_timeout_for_prompt("/testtrend smoke"))
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
