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
# MARK: FUNCTIONS
# Primary types: GuardrailSmokeTests.
# Function inventory:
# - setUp: Implements the setUp operation for this module.
# - tearDown: Implements the tearDown operation for this module.
# - test_tool_loop_auto_activates_and_executes_known_inactive_tool: Implements the test tool loop auto activates and executes known inactive tool operation for this module.
# - log: Implements the log operation for this module.
# - log_file_only: Implements the log file only operation for this module.
# - log_section: Implements the log section operation for this module.
# - log_section_file_only: Implements the log section file only operation for this module.
# - __init__: Implements the   init   operation for this module.
# - _tool_call: Implements the  tool call operation for this module.
# - fake_call_llm_chat: Implements the fake call llm chat operation for this module.
# - fake_execute_tool_call: Implements the fake execute tool call operation for this module.
# - fake_promote_selected_tools: Implements the fake promote selected tools operation for this module.
# - fake_runtime_provider: Implements the fake runtime provider operation for this module.
# - test_tool_loop_suggests_corrected_tool_name_for_invalid_request: Implements the test tool loop suggests corrected tool name for invalid request operation for this module.
# - test_test_wrapper_fails_single_prompt_on_no_results_output: Implements the test test wrapper fails single prompt on no results output operation for this module.
# - test_test_wrapper_fails_exchange_on_search_failure_output: Implements the test test wrapper fails exchange on search failure output operation for this module.
# - test_slash_command_outputs_use_ascii_arrows: Implements the test slash command outputs use ascii arrows operation for this module.
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


REPO_ROOT = Path(__file__).resolve().parents[4]
CODE_DIR  = REPO_ROOT / "KoreAgent" / "app"

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import datasets_pkg as datasets_module
from agent.tool_runtime import loop as tool_loop_module
from agent.orchestration import engine as orchestration_module
from sessions import tool_selection as tool_selection_state_module
from conversation_state import decode_background_context
from conversation_state import encode_background_context
from skill_executor import execute_tool_call
from datasets_pkg import store as datasets_store
from agent.orchestration.engine import ConversationHistory
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
from system_skills.FileAccess import file_access_skill as file_access_module
from system_skills.ToolSelection import tool_selection_skill as tool_selection_skill_module
from system_skills.FileAccess.file_access_skill import file_write
from system_skills.FileAccess.file_access_skill import file_read
from system_skills.FileAccess.file_access_skill import folder_create
from KoreLiveWeb.app.web_fetch    import fetch_page_text
from KoreLiveWeb.app.web_search   import search_web
from system_skills.SystemInfo.system_info_skill import get_system_info_string
from KoreDocs.app import korefile as koredocs_korefile
from KoreCommon import datauser_fs as datauser_fs_module
from agent.tool_runtime.loop import normalize_tool_request
from agent.tool_runtime.loop import _derive_auto_scratchpad_key
from agent.tool_runtime.loop import _extract_graph_connection_batch_from_text
from tool_result import ToolCallResult
import api.app as api_module
from input_layer import slash_commands as slash_commands_module
from input_layer import slash_command_handlers_sessions as session_handlers_module
from input_layer.routes_sessions import _runtime_config_for_prompt
from KoreTest.app.history import result_counts as _result_counts
from KoreTest.app.system import runner as test_wrapper_module
from KoreTest.app.agent_tests.unit.guardrail_support import load_test_skills_payload
from KoreTest.app.agent_tests.unit.guardrail_support import reset_guardrail_state
from utils import workspace_utils as workspace_utils_module
from utils.workspace_utils import get_user_data_dir


class GuardrailSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skills_payload = load_test_skills_payload(CODE_DIR)
        reset_guardrail_state()

    def tearDown(self) -> None:
        reset_guardrail_state()

    def test_tool_loop_auto_activates_and_executes_known_inactive_tool(self) -> None:
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
            patch.object(tool_selection_state_module, "related_tool_set", return_value=["dataset_list"]),
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
        self.assertEqual(calls, ["dataset_list"])
        self.assertIn("dataset_list", runtime_state["active"])
        joined_messages = "\n".join(str(message.get("content", "")) for message in messages)
        self.assertNotIn("It has been added to the active tool set", joined_messages)
        self.assertNotIn("Recovery still required: do not answer yet. Retry `dataset_list` now", joined_messages)

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
