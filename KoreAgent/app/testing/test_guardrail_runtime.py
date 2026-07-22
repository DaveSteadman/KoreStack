# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Internal runtime guardrail test suite for KoreAgent core modules.
#
# Uses unittest.TestCase to validate key module imports and basic function behaviour:
#   - skill_executor.execute_tool_call dispatch
#   - scratchpad read/write round-trip
#   - file_access skill validation
#   - web tools availability
#   - orchestration helpers (compact_context, assess_compact)
#
# Run manually via:
#   python -m unittest testing.test_guardrail_runtime
#   python -m pytest testing/test_guardrail_runtime.py -v
#
# The /test slash flow runs prompt suites through testing/test_wrapper.py and then
# executes focused smoke checks from test_guardrail_smoke.py.
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
from agent.tool_runtime.loop import _requires_web_evidence_guard
from skills.SystemInfo.system_info_skill import get_system_info_string
from KoreDocs.app import korefile as koredocs_korefile
from KoreCommon import datauser_fs as datauser_fs_module
from agent.tool_runtime.loop import normalize_tool_request
from agent.tool_runtime.loop import _derive_auto_scratchpad_key
from agent.tool_runtime.loop import _extract_graph_connection_batch_from_text
from tool_result import ToolCallResult
from api import app as api_module
from input_layer import slash_commands as slash_commands_module
from input_layer import slash_command_handlers_sessions as session_handlers_module
from input_layer.routes_sessions import _queue_timeout_for_prompt
from input_layer.routes_sessions import _runtime_config_for_prompt
from input_layer.slash_command_handlers_testing import _result_counts
from KoreStack import endpoint_explorer as endpoint_explorer_module
from testing import test_wrapper as test_wrapper_module
from KoreCommon import suite_paths as suite_paths_module
from utils import workspace_utils as workspace_utils_module
from utils.workspace_utils import get_user_data_dir


class GuardrailRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skills_payload = load_skills_payload(CODE_DIR / "skills" / "skills_catalog.json")
        scratchpad_clear()
        delete_session_datasets("dataset_test")
        delete_session_datasets("dataset_restore")
        delete_session_datasets("dataset_prompt")
        delete_session_datasets("dataset_filter")
        delete_session_datasets("dataset_auto")
        delete_session_datasets("dataset_paging")
        delete_session_datasets("dataset_export")
        delete_session_datasets("dataset_fulltext")
        clear_session_datasets("dataset_test")
        clear_session_datasets("dataset_restore")
        clear_session_datasets("dataset_prompt")
        clear_session_datasets("dataset_filter")
        clear_session_datasets("dataset_auto")
        clear_session_datasets("dataset_paging")
        clear_session_datasets("dataset_export")
        clear_session_datasets("dataset_fulltext")
        delete_session_datasets("dataset_load_session")
        clear_session_datasets("dataset_load_session")
        delete_session_datasets("kc_conv_701")
        clear_session_datasets("kc_conv_701")

    def tearDown(self) -> None:
        scratchpad_clear()
        delete_session_datasets("dataset_test")
        delete_session_datasets("dataset_restore")
        delete_session_datasets("dataset_prompt")
        delete_session_datasets("dataset_filter")
        delete_session_datasets("dataset_auto")
        delete_session_datasets("dataset_paging")
        delete_session_datasets("dataset_export")
        delete_session_datasets("dataset_fulltext")
        clear_session_datasets("dataset_test")
        clear_session_datasets("dataset_restore")
        clear_session_datasets("dataset_prompt")
        clear_session_datasets("dataset_filter")
        clear_session_datasets("dataset_auto")
        clear_session_datasets("dataset_paging")
        clear_session_datasets("dataset_export")
        clear_session_datasets("dataset_fulltext")
        delete_session_datasets("dataset_load_session")
        clear_session_datasets("dataset_load_session")
        delete_session_datasets("kc_conv_701")
        clear_session_datasets("kc_conv_701")

    def test_delegate_rejects_invalid_json_result_before_dataset_persistence(self) -> None:
        record = {
            "task_id": "dlg_test_invalid_json",
            "parent_session_id": "delegate_validation_test",
            "data_out": {
                "result_target": "dataset:delegate_validation",
                "result_format": "json array of objects",
            },
        }

        saved_keys, datasets, artifacts, error, normalized = delegate_runtime_module._apply_result_target(
            record,
            "not valid json",
        )

        self.assertEqual(saved_keys, [])
        self.assertEqual(datasets, [])
        self.assertEqual(artifacts, [])
        self.assertIn("not valid JSON", error)
        self.assertEqual(normalized, "not valid json")

    def test_skills_catalog_local_entries_include_schema_and_template(self) -> None:
        fake_config = SimpleNamespace(skills_payload=self.skills_payload)

        with patch.object(api_module, "_config", fake_config):
            with patch.object(api_module, "get_selected_tools", return_value=[]):
                with patch.object(api_module.mcp_client, "get_mcp_tool_definitions", return_value=[]):
                    with patch.object(api_module.mcp_client, "get_mcp_tool_index", return_value={}):
                        payload = api_module.skills_catalog_get()

        entries = payload["entries"]
        tools_active_add = next(item for item in entries if item["tool_name"] == "tools_active_add")
        delegate         = next(item for item in entries if item["tool_name"] == "delegate")
        delegate_status  = next(item for item in entries if item["tool_name"] == "delegate_status")
        file_read        = next(item for item in entries if item["tool_name"] == "file_read")
        self.assertEqual(tools_active_add["call_type"], "python")
        self.assertEqual(tools_active_add["parameters_schema"]["type"], "object")
        self.assertEqual(tools_active_add["parameters_schema"]["properties"]["tool_names"]["type"], "array")
        self.assertEqual(tools_active_add["invoke_template"]["tool_names"], ["example"])
        self.assertEqual(delegate["parameters_schema"]["properties"]["task_in"]["type"], "string")
        self.assertEqual(delegate["parameters_schema"]["properties"]["data_in"]["type"], "object")
        self.assertEqual(delegate["parameters_schema"]["properties"]["process"]["type"], "object")
        self.assertEqual(delegate_status["parameters_schema"]["properties"]["task_id"]["type"], "string")
        self.assertEqual(file_read["parameters_schema"]["properties"]["max_chars"]["default"], 8000)
        self.assertEqual(file_read["invoke_template"]["max_chars"], 8000)

    def test_delegate_is_always_on(self) -> None:
        self.assertIn("delegate", tool_selection_state_module.ALWAYS_ON_TOOL_NAMES)

    def test_delegate_gen2_queues_child_and_collects_parent_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path     = Path(temp_dir)
            control_dir   = temp_path / "controldata"
            logs_dir      = temp_path / "logs"
            parent_log    = logs_dir / "parent.log"
            child_payload = {
                "skills": [
                    {
                        "skill_name":         "Scratchpad",
                        "purpose":            "Load scratchpad values.",
                        "module":             "KoreAgent/app/system_skills/Scratchpad/scratchpad_skill.py",
                        "functions":          ["scratchpad_load(key: str)"],
                        "param_descriptions": {"scratchpad_load": {"key": "scratchpad key"}},
                    },
                ],
            }
            captured: dict[str, object] = {}

            def _fake_enqueue(_name, _kind, callback, **_kwargs):
                callback()
                return True

            def _fake_orchestrate_prompt(**kwargs):
                captured["child_session_id"]   = get_active_session_id()
                captured["child_prompt"]       = kwargs["user_prompt"]
                captured["copied_parent_note"] = scratchpad_load("parent_note")
                return ("child summary", 11, 7, True, 3.25)

            previous_logger             = getattr(delegate_runtime_module._delegate_tls, "logger", None)
            previous_config             = getattr(delegate_runtime_module._delegate_tls, "config", None)
            previous_conversation_entry = getattr(delegate_runtime_module._delegate_tls, "conversation_entry", None)

            try:
                with delegate_runtime_module.SessionLogger(parent_log) as parent_logger:
                    delegate_runtime_module._delegate_tls.logger             = parent_logger
                    delegate_runtime_module._delegate_tls.config             = SimpleNamespace(resolved_model="test-model", num_ctx=4096)
                    delegate_runtime_module._delegate_tls.conversation_entry = {"id": 321}

                    with bind_session("delegate_parent_test"):
                        scratchpad_save("parent_note", "alpha source note")

                        with patch.object(delegate_runtime_module, "get_controldata_dir", return_value=control_dir):
                            with patch.object(delegate_runtime_module, "get_logs_dir", return_value=logs_dir):
                                with patch.object(delegate_runtime_module, "load_skills_payload", return_value=child_payload):
                                    with patch("scheduler.scheduler.task_queue.enqueue", side_effect=_fake_enqueue):
                                        with patch.object(delegate_runtime_module, "orchestrate_prompt", side_effect=_fake_orchestrate_prompt):
                                            queued = delegate_skill_module.delegate(
                                                task_in  = "Summarise the parent note.",
                                                data_in  = {"scratchpad_keys": ["parent_note"]},
                                                process  = {
                                                    "tools_allowlist": ["scratchpad_load"],
                                                    "max_iterations":  2,
                                                },
                                                data_out = {"result_target": "scratchpad:child_result"},
                                            )

                                            self.assertEqual(queued["status"], "queued")
                                            self.assertTrue(str(queued["task_id"]).startswith("dlg_"))

                                            status = delegate_skill_module.delegate_status(queued["task_id"])
                                            result = delegate_skill_module.delegate_collect(queued["task_id"])
                                            saved  = scratchpad_load("child_result")

                self.assertEqual(status["status"], "completed")
                self.assertTrue(result["ready"])
                self.assertEqual(result["result"]["status"], "ok")
                self.assertEqual(result["result"]["summary"], "child summary")
                self.assertIn("child_result", result["result"]["saved_keys"])
                self.assertEqual(saved, "child summary")
                self.assertEqual(captured["copied_parent_note"], "alpha source note")
                self.assertTrue(str(captured["child_session_id"]).startswith("delegate_task_dlg_"))
                self.assertIn("Task In:", str(captured["child_prompt"]))
                self.assertIn("Data Out:", str(captured["child_prompt"]))
            finally:
                delegate_runtime_module._delegate_tls.logger             = previous_logger
                delegate_runtime_module._delegate_tls.config             = previous_config
                delegate_runtime_module._delegate_tls.conversation_entry = previous_conversation_entry

    def test_delegate_gen2_infers_dataset_target_from_alias_field(self) -> None:
        task_in, data_in, process, data_out = delegate_runtime_module._coerce_task_contract(
            task_in  = "Generate a JSON array and save it to a dataset named 'delegate_planets'.",
            data_in  = None,
            process  = {"tools_allowlist": ["dataset_save"]},
            data_out = {"target_dataset": "delegate_planets"},
        )

        self.assertEqual(task_in, "Generate a JSON array and save it to a dataset named 'delegate_planets'.")
        self.assertEqual(data_in["scratchpad_keys"], [])
        self.assertEqual(process["tools_allowlist"], ["dataset_save"])
        self.assertEqual(data_out["result_target"], "dataset:delegate_planets")

    def test_skills_catalog_mcp_entries_include_schema_and_template(self) -> None:
        fake_config = SimpleNamespace(skills_payload=self.skills_payload)
        mcp_defs = [
            {
                "type": "function",
                "function": {
                    "name": "demo_lookup",
                    "description": "Look up a record.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer", "default": 5},
                            "domains": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["query"],
                    },
                },
            },
        ]
        mcp_idx = {
            "demo_lookup": {
                "connection": "Demo MCP",
                "purpose":    "Demo lookup purpose.",
            },
        }

        with patch.object(api_module, "_config", fake_config):
            with patch.object(api_module, "get_selected_tools", return_value=[]):
                with patch.object(api_module.mcp_client, "get_mcp_tool_definitions", return_value=mcp_defs):
                    with patch.object(api_module.mcp_client, "get_mcp_tool_index", return_value=mcp_idx):
                        payload = api_module.skills_catalog_get()

        entry = next(item for item in payload["entries"] if item["tool_name"] == "demo_lookup")
        self.assertEqual(entry["call_type"], "mcp")
        self.assertEqual(entry["description"], "Look up a record.")
        self.assertEqual(entry["parameters_schema"]["properties"]["limit"]["default"], 5)
        self.assertEqual(
            entry["invoke_template"],
            {"query": "example search", "limit": 5, "domains": ["example"]},
        )

    def test_note_tool_used_promotes_in_memory_without_persisting(self) -> None:
        conversation_entry = {"tools_active": ["tool_a", "tool_b", "tool_c"]}
        patched_payloads: list[tuple[str, dict]] = []

        with patch.object(tool_selection_state_module, "_kc_request_json", side_effect=lambda *args, **kwargs: patched_payloads.append((args[0], kwargs.get("payload") or {})) or None):
            tool_selection_state_module.clear_session_tools_active("selection_cache_test")
            tool_selection_state_module.note_tool_used(
                "tool_c",
                session_id="selection_cache_test",
                conversation_entry=conversation_entry,
            )
            selected = tool_selection_state_module.get_selected_tools(
                session_id="selection_cache_test",
                conversation_entry=conversation_entry,
            )

        self.assertEqual(selected[:3], ["tool_c", "tool_a", "tool_b"])
        self.assertEqual(conversation_entry["tools_active"][:3], ["tool_c", "tool_a", "tool_b"])
        self.assertEqual(patched_payloads, [])

    def test_tools_catalog_list_ranks_trigger_matches(self) -> None:
        fake_payload = {
            "skills": [
                {
                    "skill_name": "Dataset Tools",
                    "purpose": "List saved datasets.",
                    "module": "KoreAgent/app/system_skills/Datasets/datasets_skill.py",
                    "functions": ["dataset_list()"],
                    "param_descriptions": {},
                    "triggers": ["list datasets", "dataset inventory"],
                    "trigger_keyword": "datasets",
                    "origin": "local",
                    "availability": "configured",
                    "role": "optional",
                    "trust_boundary": "internal",
                },
                {
                    "skill_name": "File Tools",
                    "purpose": "List files from the workspace.",
                    "module": "KoreAgent/app/system_skills/FileAccess/file_access_skill.py",
                    "functions": ["file_find(keywords: list[str], search_root: str = \"\")"],
                    "param_descriptions": {"file_find": {"keywords": "keywords", "search_root": "root"}},
                    "triggers": ["find files"],
                    "trigger_keyword": "files",
                    "origin": "local",
                    "availability": "configured",
                    "role": "optional",
                    "trust_boundary": "internal",
                },
            ],
        }

        with patch.object(tool_selection_skill_module, "load_skills_payload", return_value=fake_payload):
            with patch.object(tool_selection_state_module, "get_selected_tools", return_value=[]):
                with patch.object(tool_selection_state_module.mcp_client, "get_mcp_tool_index", return_value={}):
                    with patch.object(tool_selection_state_module.mcp_client, "get_mcp_tool_definitions", return_value=[]):
                        results = tool_selection_skill_module.tools_catalog_list(filter_text="list datasets", max_items=5, include_mcp=False)

        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "dataset_list")

    def test_tool_selection_respects_web_skill_filter(self) -> None:
        fake_payload = {
            "skills": [
                {
                    "skill_name": "WebSearch",
                    "purpose": "Search the web.",
                    "module": "KoreAgent/app/skills/WebSearch/web_search_skill.py",
                    "functions": ["search_web_text(query: str = \"\")"],
                    "param_descriptions": {"search_web_text": {"query": "query"}},
                    "triggers": ["search the web"],
                    "trigger_keyword": "web",
                    "origin": "local",
                    "availability": "configured",
                    "role": "optional",
                    "trust_boundary": "internal",
                },
                {
                    "skill_name": "Datasets",
                    "purpose": "List datasets.",
                    "module": "KoreAgent/app/system_skills/Datasets/datasets_skill.py",
                    "functions": ["dataset_list()"],
                    "param_descriptions": {},
                    "triggers": ["list datasets"],
                    "trigger_keyword": "datasets",
                    "origin": "local",
                    "availability": "configured",
                    "role": "optional",
                    "trust_boundary": "internal",
                },
            ],
        }

        with patch.object(tool_selection_skill_module, "load_skills_payload", return_value=fake_payload):
            with patch.object(tool_selection_skill_module, "get_web_skills_enabled", return_value=False):
                with patch.object(tool_selection_skill_module, "get_selected_tools", return_value=[]):
                    with patch.object(
                        tool_selection_skill_module,
                        "promote_selected_tools",
                        return_value={
                            "added": ["dataset_list"],
                            "promoted": [],
                            "evicted": [],
                            "active_tools": ["dataset_list"],
                        },
                    ):
                        listed = tool_selection_skill_module.tools_catalog_list(filter_text="", max_items=20, include_mcp=False)
                        added = tool_selection_skill_module.tools_active_add(["search_web_text", "dataset_list"])

        listed_names = [entry["name"] for entry in listed]
        self.assertIn("dataset_list", listed_names)
        self.assertNotIn("search_web_text", listed_names)
        self.assertIn("dataset_list", added["added"] + added["promoted"] + added["already_active_before_call"])
        self.assertIn("search_web_text", added["unknown"])

    def test_tool_selection_respects_koreliveweb_mcp_filter(self) -> None:
        fake_payload = {
            "skills": [
                {
                    "skill_name": "Datasets",
                    "purpose": "List datasets.",
                    "module": "KoreAgent/app/system_skills/Datasets/datasets_skill.py",
                    "functions": ["dataset_list()"],
                    "param_descriptions": {},
                    "triggers": ["list datasets"],
                    "trigger_keyword": "datasets",
                    "origin": "local",
                    "availability": "configured",
                    "role": "optional",
                    "trust_boundary": "internal",
                },
            ],
        }
        mcp_defs = [
            {
                "type": "function",
                "function": {
                    "name": "search_web_text",
                    "description": "Search the web.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            },
        ]
        mcp_idx = {
            "search_web_text": {
                "connection": "KoreLiveWeb",
                "purpose": "Live web search",
            },
        }

        with patch.object(tool_selection_skill_module, "load_skills_payload", return_value=fake_payload):
            with patch.object(tool_selection_skill_module, "get_web_skills_enabled", return_value=False):
                with patch("orchestration.get_web_skills_enabled", return_value=False):
                    with patch.object(tool_selection_skill_module, "get_selected_tools", return_value=[]):
                        with patch.object(
                            tool_selection_skill_module,
                            "promote_selected_tools",
                            return_value={
                                "added": ["dataset_list"],
                                "promoted": [],
                                "evicted": [],
                                "active_tools": ["dataset_list"],
                            },
                        ):
                            with patch.object(tool_selection_state_module.mcp_client, "get_mcp_tool_index", return_value=mcp_idx):
                                with patch.object(tool_selection_state_module.mcp_client, "get_mcp_tool_definitions", return_value=mcp_defs):
                                    listed = tool_selection_skill_module.tools_catalog_list(filter_text="", max_items=20, include_mcp=True)
                                    added = tool_selection_skill_module.tools_active_add(["search_web_text", "dataset_list"])

        listed_names = [entry["name"] for entry in listed]
        self.assertIn("dataset_list", listed_names)
        self.assertNotIn("search_web_text", listed_names)
        self.assertIn("dataset_list", added["added"] + added["promoted"] + added["already_active_before_call"])
        self.assertIn("search_web_text", added["unknown"])

    def test_write_file_writes_system_info_csv(self) -> None:
        user_data_dir = get_user_data_dir()
        output_path = user_data_dir / "test_systemstats_regression.csv"
        expected_label = f"{user_data_dir.name}/test_systemstats_regression.csv"

        if output_path.exists():
            output_path.unlink()

        try:
            result = file_write("test_systemstats_regression.csv", get_system_info_string())
            self.assertEqual(result, f"Wrote {expected_label}")
            self.assertTrue(output_path.exists())

            content = output_path.read_text(encoding="utf-8")
            self.assertIn("os=", content)
            self.assertIn("python=", content)
        finally:
            if output_path.exists():
                output_path.unlink()

    def test_extract_graph_connection_batch_from_final_answer(self) -> None:
        text = """
[
  ["Office for Budget Responsibility", "reports_to", "U.K. Government"],
  {"subject": "HM Treasury", "predicate": "reports_to", "object": "U.K. Government"}
]
"""

        connections = _extract_graph_connection_batch_from_text(text)

        self.assertEqual(
            connections,
            [
                {
                    "start": "Office for Budget Responsibility",
                    "connection": "reports_to",
                    "end": "U.K. Government",
                },
                {
                    "start": "HM Treasury",
                    "connection": "reports_to",
                    "end": "U.K. Government",
                },
            ],
        )

    def test_graph_write_guard_forces_tool_call_for_printed_triples(self) -> None:
        calls: list[tuple[str, dict]] = []

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

        responses = [
            _FakeResult('[["A", "reports_to", "B"]]'),
            _FakeResult("Added to graph."),
        ]

        def fake_call_llm_chat(**_kwargs):
            return responses.pop(0)

        def fake_execute_tool_call(func_name, arguments, *_args):
            calls.append((func_name, arguments))
            return ToolCallResult(
                tool=func_name,
                function=func_name,
                module="mcp_client",
                arguments=arguments,
                result={"accepted": 1, "errors": []},
            )

        config = SimpleNamespace(
            resolved_model="test-model",
            max_iterations=3,
            num_ctx=8192,
            skills_payload={"skills": []},
        )
        tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": "graph_connection_create_many",
                    "description": "Create graph connections",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "add these triples to the graph"},
        ]
        context_map = [
            {"round": 0, "role": "sys", "label": "system", "chars": 6, "auto_key": None, "msg_idx": 0},
            {"round": 0, "role": "user", "label": "prompt", "chars": 32, "auto_key": None, "msg_idx": 1},
        ]

        with patch.object(tool_loop_module, "execute_tool_call", side_effect=fake_execute_tool_call):
            final_response, _prompt_tokens, _completion_tokens, run_success, _tps, tool_outputs = tool_loop_module.run_tool_loop(
                config=config,
                messages=messages,
                tool_defs=tool_defs,
                catalog_gates={},
                context_map=context_map,
                user_prompt="add these triples to the graph",
                logger=_DummyLogger(),
                quiet=True,
                call_llm_chat=fake_call_llm_chat,
                stop_requested=lambda: False,
                clear_stop=lambda: None,
            )

        self.assertTrue(run_success)
        self.assertEqual(final_response, "Added to graph.")
        self.assertEqual(len(tool_outputs), 1)
        self.assertEqual(calls[0][0], "graph_connection_create_many")
        self.assertEqual(calls[0][1], {"connections": [{"start": "A", "connection": "reports_to", "end": "B"}]})

    def test_read_file_accepts_workspace_relative_data_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            data_dir = tmp_root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            target = data_dir / "ai-sites.json"
            target.write_text('{"ok": true}', encoding="utf-8")

            with patch.dict(os.environ, {"KORE_SUITE_ROOT": str(tmp_root), "KORE_SUITE_DATAUSER": str(data_dir)}):
                datauser_fs_module.get_suite_root.cache_clear()
                datauser_fs_module.get_suite_config_file.cache_clear()
                datauser_fs_module.get_datauser_root.cache_clear()
                datauser_fs_module._load_path_overrides.cache_clear()
                try:
                    result = file_read("data/ai-sites.json")
                finally:
                    datauser_fs_module.get_suite_root.cache_clear()
                    datauser_fs_module.get_suite_config_file.cache_clear()
                    datauser_fs_module.get_datauser_root.cache_clear()
                    datauser_fs_module._load_path_overrides.cache_clear()

        self.assertEqual(result, '{"ok": true}')

    def test_create_folder_accepts_workspace_relative_data_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            data_dir = tmp_root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            with patch.dict(os.environ, {"KORE_SUITE_ROOT": str(tmp_root), "KORE_SUITE_DATAUSER": str(data_dir)}):
                datauser_fs_module.get_suite_root.cache_clear()
                datauser_fs_module.get_suite_config_file.cache_clear()
                datauser_fs_module.get_datauser_root.cache_clear()
                datauser_fs_module._load_path_overrides.cache_clear()
                try:
                    result = folder_create("data/2026-04-05")
                finally:
                    datauser_fs_module.get_suite_root.cache_clear()
                    datauser_fs_module.get_suite_config_file.cache_clear()
                    datauser_fs_module.get_datauser_root.cache_clear()
                    datauser_fs_module._load_path_overrides.cache_clear()

            created = data_dir / "2026-04-05"
            self.assertTrue(created.exists())
            self.assertEqual(result, "Created folder: data/2026-04-05")

    def test_workspace_utils_reads_folder_overrides_from_bootstrap_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            # Bootstrap file is config/llm_config.json (returned by get_bootstrap_defaults_file)
            bootstrap = tmp_root / "config" / "llm_config.json"
            bootstrap.parent.mkdir(parents=True, exist_ok=True)
            bootstrap.write_text(
                '{\n'
                '  "DataRootFolder": "suite_data"\n'
                '}\n',
                encoding="utf-8",
            )

            workspace_utils_module.get_bootstrap_defaults_file.cache_clear()
            workspace_utils_module.load_runtime_config.cache_clear()
            workspace_utils_module._load_path_overrides.cache_clear()
            workspace_utils_module.get_controldata_dir.cache_clear()
            workspace_utils_module.get_user_data_dir.cache_clear()

            try:
                with patch.object(workspace_utils_module, "get_workspace_root", return_value=tmp_root):
                    self.assertEqual(workspace_utils_module.get_controldata_dir(), (tmp_root / "suite_data" / "datacontrol").resolve())
                    self.assertEqual(workspace_utils_module.get_user_data_dir(), (tmp_root / "suite_data" / "datauser").resolve())
            finally:
                workspace_utils_module.get_bootstrap_defaults_file.cache_clear()
                workspace_utils_module.load_runtime_config.cache_clear()
                workspace_utils_module._load_path_overrides.cache_clear()
                workspace_utils_module.get_controldata_dir.cache_clear()
                workspace_utils_module.get_user_data_dir.cache_clear()

    def test_web_slash_commands_mutate_shared_runtime_config(self) -> None:
        config = OrchestratorConfig(
            resolved_model="nemotron-cascade-2:latest",
            num_ctx=131072,
            max_iterations=3,
            skills_payload=self.skills_payload,
        )

        slash_config = _runtime_config_for_prompt(config, "/llmserverconfig model gemma4:26b")
        prompt_config = _runtime_config_for_prompt(config, "hello")

        self.assertIs(slash_config, config)
        self.assertIsNot(prompt_config, config)

    def test_testtrend_uses_summary_counts_for_legacy_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "test_results_20260504_102420_test_koredata_search.csv"
            summary_path = Path(tmp) / "summary_20260504_102420_test_koredata_search.md"
            csv_path.write_text(
                '"timestamp","assert_result","exit_code","final_output"\n'
                '"2026-05-04 10:24:21","PASS","0","ok"\n'
                '"2026-05-04 10:24:22","PASS","0","ok"\n',
                encoding="utf-8",
            )
            summary_path.write_text("Run: now  |  Passed: **1/2**  |  Wall-clock: 1s\n", encoding="utf-8")

            rows = [{"assert_result": "PASS", "exit_code": "0", "final_output": "ok"} for _ in range(2)]

            self.assertEqual(_result_counts(rows, csv_path), (2, 1, 1, 0))

    def test_testtrend_prefers_persisted_wrapper_outcome(self) -> None:
        rows = [
            {"assert_result": "PASS", "passed": "PASS", "failure_reason": "", "exit_code": "0", "final_output": "ok"},
            {"assert_result": "PASS", "passed": "FAIL", "failure_reason": "Search returned no results", "exit_code": "0", "final_output": "No results were found."},
        ]

        self.assertEqual(_result_counts(rows, Path("missing.csv")), (2, 1, 1, 0))

    def test_execute_tool_call_runs_datetime(self) -> None:
        result = execute_tool_call(
            tool_name="get_datetime_data",
            arguments={},
            skills_payload=self.skills_payload,
        )
        self.assertEqual(result["function"], "get_datetime_data")
        self.assertIsNotNone(result["result"])
        self.assertNotIn("error", str(result["result"]).lower())

    def test_execute_tool_call_allows_known_inactive_tool(self) -> None:
        result = execute_tool_call(
            tool_name="get_datetime_data",
            arguments={},
            skills_payload=self.skills_payload,
            active_tool_names={"tools_catalog_list", "tools_active_add"},
        )

        self.assertEqual(result["function"], "get_datetime_data")
        self.assertIsNotNone(result["result"])

    def test_execute_tool_call_unknown_tool_returns_alternatives(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            execute_tool_call(
                tool_name="koredec_table_read",
                arguments={},
                skills_payload=self.skills_payload,
                active_tool_names={"tools_catalog_list", "tools_active_add"},
            )

        self.assertIn("not found in skills catalog", str(ctx.exception))
        self.assertIn("Closest alternatives:", str(ctx.exception))
        self.assertIn("tools_active_add", str(ctx.exception))

    def test_build_tool_definitions_has_entries(self) -> None:
        tool_defs = build_tool_definitions(self.skills_payload)
        self.assertGreater(len(tool_defs), 0)
        for tool in tool_defs:
            self.assertEqual(tool["type"], "function")
            self.assertIn("name", tool["function"])
            self.assertIn("parameters", tool["function"])
            self.assertEqual(tool["function"]["parameters"]["type"], "object")

    def test_loaded_skills_payload_infers_tool_classification_metadata(self) -> None:
        skills = self.skills_payload.get("skills", [])
        self.assertGreater(len(skills), 0)

        system_skill = next((skill for skill in skills if skill.get("is_system_skill") is True), None)
        local_skill  = next((skill for skill in skills if skill.get("is_system_skill") is False), None)

        self.assertIsNotNone(system_skill)
        self.assertIsNotNone(local_skill)
        self.assertEqual(system_skill["origin"], "builtin")
        self.assertEqual(system_skill["availability"], "guaranteed")
        self.assertEqual(system_skill["role"], "core")
        self.assertEqual(system_skill["trust_boundary"], "internal")
        self.assertEqual(local_skill["origin"], "local")
        self.assertEqual(local_skill["availability"], "configured")
        self.assertEqual(local_skill["role"], "optional")
        self.assertEqual(local_skill["trust_boundary"], "internal")

    def test_mcp_connections_prefer_new_config_and_skip_disabled_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "korestack_config.json"
            config_path.write_text(
                '{\n'
                '  "mcp_servers": [\n'
                '    {"name": "Legacy", "url": "http://legacy/mcp"}\n'
                '  ],\n'
                '  "mcp_connections": [\n'
                '    {"name": "KoreData", "url": "http://data/mcp", "purpose": "reference", "expected_prefix": "koredata_", "allowed_tools": ["koredata_search"], "blocked_tools": ["koredata_delete"]},\n'
                '    {"name": "KoreDocs", "url": "http://docs/mcp", "enabled": false}\n'
                '  ]\n'
                '}\n',
                encoding="utf-8",
            )

            servers = mcp_client._load_server_config(config_path)

        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["name"], "KoreData")
        self.assertEqual(servers[0]["url"], "http://data/mcp")
        self.assertEqual(servers[0]["purpose"], "reference")
        self.assertEqual(servers[0]["expected_prefix"], "koredata_")
        self.assertEqual(servers[0]["allowed_tools"], ["koredata_search"])
        self.assertEqual(servers[0]["blocked_tools"], ["koredata_delete"])

    def test_mcp_connections_accept_legacy_mcp_servers_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "korestack_config.json"
            config_path.write_text(
                '{\n'
                '  "mcp_servers": [\n'
                '    {"name": "Legacy", "url": "http://legacy/mcp", "tool_prefix": "legacy_"}\n'
                '  ]\n'
                '}\n',
                encoding="utf-8",
            )

            servers = mcp_client._load_server_config(config_path)

        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["name"], "Legacy")
        self.assertEqual(servers[0]["expected_prefix"], "legacy_")

    def test_mcp_connections_include_tool_classification_metadata(self) -> None:
        server = mcp_client._normalize_connection({"name": "KoreData", "url": "http://data/mcp"})

        self.assertEqual(server["origin"], "remote_mcp")
        self.assertEqual(server["availability"], "discovered")
        self.assertEqual(server["role"], "external")
        self.assertEqual(server["trust_boundary"], "external")

    def test_kc_direct_session_id_maps_to_conversation_id(self) -> None:
        self.assertEqual(api_module._kc_conversation_id_for_session("kc_conv_4"), 4)
        self.assertIsNone(api_module._kc_conversation_id_for_session("web_123"))
        self.assertIsNone(api_module._kc_conversation_id_for_session("kc_conv_task"))

    def test_kc_get_conversation_for_direct_session_uses_conversation_endpoint(self) -> None:
        with patch.object(api_module, "_kc_get", return_value={"id": 4, "subject": "XXX"}) as mock_get:
            conv = api_module._kc_get_conversation_for_session("kc_conv_4")

        self.assertEqual(conv, {"id": 4, "subject": "XXX"})
        mock_get.assert_called_once_with("/conversations/4")

    def test_request_switch_uses_direct_session_for_non_webchat_conversation(self) -> None:
        body = api_module.SessionSwitchRequest(name="XXX")
        previous = api_module._pending_switch
        try:
            api_module._pending_switch = None
            with (
                patch("input_layer.slash_command_handlers_sessions._list_all_conversations", return_value=[
                    {"id": 4, "subject": "XXX", "external_id": "task:XXX", "channel_type": "scheduled"},
                ]),
                patch("input_layer.slash_command_handlers_sessions._display_name", return_value="XXX"),
            ):
                result = api_module.post_request_switch(body)

            self.assertEqual(result, {"ok": True})
            self.assertEqual(api_module._pending_switch, {"session_id": "kc_conv_4", "name": "XXX"})
        finally:
            api_module._pending_switch = previous

    def test_request_switch_uses_conversation_id_for_new_webchat_conversation(self) -> None:
        body = api_module.SessionSwitchRequest(name="", conversation_id=7)
        previous = api_module._pending_switch
        try:
            api_module._pending_switch = None
            with (
                patch("input_layer.slash_command_handlers_sessions._list_all_conversations", return_value=[
                    {"id": 7, "subject": "New conversation", "external_id": "", "channel_type": "webchat"},
                ]),
                patch("input_layer.slash_command_handlers_sessions._display_name", return_value="New conversation"),
            ):
                result = api_module.post_request_switch(body)

            self.assertEqual(result, {"ok": True})
            self.assertEqual(api_module._pending_switch, {"session_id": "kc_conv_7", "name": "New conversation"})
        finally:
            api_module._pending_switch = previous

    def test_suite_mcp_service_refs_resolve_urls(self) -> None:
        config = workspace_utils_module._flatten_suite_config({
            "network": {"host": "127.0.0.1"},
            "services": {
                "koredatagateway": {"port": 9603},
                "koredocs": {"port": 9610},
                "koregraph": {"port": 9608},
            },
            "mcp": {
                "connections": [
                    {"name": "KoreData", "service": "koredatagateway", "path": "/mcp", "expected_prefix": "koredata_"},
                    {"name": "KoreDocs", "service": "koredocs", "path": "/mcp", "expected_prefix": "koredocs_"},
                ]
            },
        })

        workspace_utils_module._resolve_mcp_service_refs(config)

        self.assertEqual(
            config["mcp_connections"],
            [
                {"name": "KoreData", "service": "koredatagateway", "path": "/mcp", "expected_prefix": "koredata_", "url": "http://127.0.0.1:9603/mcp"},
                {"name": "KoreDocs", "service": "koredocs", "path": "/mcp", "expected_prefix": "koredocs_", "url": "http://127.0.0.1:9610/mcp"},
            ],
        )

    def test_runtime_config_merge_keeps_default_service_ports_for_mcp_refs(self) -> None:
        merged: dict = {}
        workspace_utils_module._merge_runtime_config_layer(
            merged,
            workspace_utils_module._flatten_suite_config({
                "network": {"host": "127.0.0.1"},
                "services": {
                    "koredatagateway": {"port": 9603},
                    "koredocs": {"port": 9610},
                },
                "mcp": {
                    "connections": [
                        {"name": "KoreData", "service": "koredatagateway", "path": "/mcp", "expected_prefix": "koredata_"},
                        {"name": "KoreDocs", "service": "koredocs", "path": "/mcp", "expected_prefix": "koredocs_"},
                    ]
                },
            }),
        )
        workspace_utils_module._merge_runtime_config_layer(
            merged,
            workspace_utils_module._flatten_suite_config({
                "services": {
                    "koreagent": {"port": 9601},
                }
            }),
        )

        workspace_utils_module._resolve_mcp_service_refs(merged)

        self.assertEqual(
            merged["mcp_connections"],
            [
                {"name": "KoreData", "service": "koredatagateway", "path": "/mcp", "expected_prefix": "koredata_", "url": "http://127.0.0.1:9603/mcp"},
                {"name": "KoreDocs", "service": "koredocs", "path": "/mcp", "expected_prefix": "koredocs_", "url": "http://127.0.0.1:9610/mcp"},
            ],
        )

    def test_suite_urls_map_includes_koreliveweb(self) -> None:
        suite_paths_module.load_suite_config.cache_clear()
        with patch.object(
            suite_paths_module,
            "load_suite_config",
            return_value={
                "network": {"host": "127.0.0.1"},
                "services": {
                    "korestack": {"port": 9600},
                    "koreagent": {"port": 9601},
                    "korechat": {"port": 9602},
                    "koredatagateway": {"port": 9603},
                    "koredocs": {"port": 9610},
                    "korecode": {"port": 9611},
                    "koreliveweb": {"port": 9613},
                },
            },
        ):
            urls = suite_paths_module.get_suite_urls_map()

        self.assertEqual(urls["koreliveweb"], "http://127.0.0.1:9613/")

    def test_endpoint_explorer_targets_include_koreliveweb(self) -> None:
        targets = endpoint_explorer_module.service_targets(
            {
                "network": {"host": "127.0.0.1"},
                "services": {
                    "korestack": {"port": 9600},
                    "koreagent": {"port": 9601},
                    "koreliveweb": {"port": 9613, "enabled": True},
                },
            },
            "http://127.0.0.1:9600/",
        )

        liveweb = next(item for item in targets if item["key"] == "koreliveweb")
        self.assertEqual(liveweb["label"], "KoreLiveWeb")
        self.assertEqual(liveweb["base_url"], "http://127.0.0.1:9613")

    def test_mcp_connection_error_formatter_unwraps_exception_groups(self) -> None:
        inner = ConnectionRefusedError("connection refused")
        outer = ExceptionGroup("unhandled errors in a TaskGroup", [inner])

        message = mcp_client._format_connection_error(outer)

        self.assertEqual(message, "connection refused")

    def test_mcp_enumeration_ignores_duplicate_tool_names_from_later_connections(self) -> None:
        async def fake_list_tools(server):
            name = server["name"]
            defs = [
                {"type": "function", "function": {"name": "shared_tool", "description": name, "parameters": {"type": "object", "properties": {}}}},
                {"type": "function", "function": {"name": f"{name}_only", "description": name, "parameters": {"type": "object", "properties": {}}}},
            ]
            index = {
                "shared_tool": {"url": server["url"], "connection": name},
                f"{name}_only": {"url": server["url"], "connection": name},
            }
            return defs, index

        servers = [
            {"name": "first", "url": "http://first/mcp"},
            {"name": "second", "url": "http://second/mcp"},
        ]

        with patch.object(mcp_client, "_list_tools_async", side_effect=fake_list_tools):
            defs, index = __import__("asyncio").run(mcp_client._enumerate_all_servers(servers))

        tool_names = [tool["function"]["name"] for tool in defs]
        self.assertEqual(tool_names.count("shared_tool"), 1)
        self.assertIn("first_only", tool_names)
        self.assertIn("second_only", tool_names)
        self.assertEqual(index["shared_tool"]["connection"], "first")

    def test_normalize_tool_request_rewrites_assistant_delegate_wrapper(self) -> None:
        func_name, arguments, note = normalize_tool_request(
            "assistant",
            {
                "name": "delegate",
                "arguments": {
                    "task": "Find the latest advancements in quantum computing and provide a concise summary.",
                    "process": {"tools_allowlist": ["search_web"]},
                },
            },
        )

        self.assertEqual(func_name, "delegate")
        self.assertIn("task_in", arguments)
        self.assertNotIn("task", arguments)
        self.assertEqual(arguments["task_in"], "Find the latest advancements in quantum computing and provide a concise summary.")
        self.assertEqual(arguments["process"], {"tools_allowlist": ["search_web"]})
        self.assertIn("assistant(...) -> delegate(...)", note or "")

    def test_fetch_page_text_query_mode_falls_back_to_raw_page_text(self) -> None:
        html_text = "<html><body>unused</body></html>"
        body_text = (
            "# BBC News\n\n"
            "### First headline from the page\n\n"
            "A paragraph with enough words to survive extraction and give the caller usable page content.\n\n"
            "### Second headline from the page\n\n"
            "Another paragraph with enough words to survive extraction and keep the page useful."
        )

        with patch("KoreLiveWeb.app.web_fetch._fetch_html", return_value=(html_text, "https://www.bbc.co.uk/news")):
            with patch("KoreLiveWeb.app.web_fetch._extract_content", return_value=("BBC News", body_text)):
                with patch("KoreLiveWeb.app.web_fetch._get_active_model", return_value="gpt-oss:20b"):
                    with patch("KoreLiveWeb.app.web_fetch._get_active_num_ctx", return_value=131072):
                        with patch(
                            "KoreLiveWeb.app.web_fetch._call_llm_chat",
                            return_value=SimpleNamespace(response="Not found on this page."),
                        ):
                            result = fetch_page_text(
                                url="https://news.bbc.co.uk",
                                max_words=400,
                                timeout_seconds=30,
                                query="headlines",
                            )

        self.assertIn("# BBC News", result)
        self.assertIn("### First headline from the page", result)
        self.assertIn("### Second headline from the page", result)
        self.assertNotEqual(result.strip(), "Not found on this page.")

    def test_fetch_page_text_query_miss_returns_large_raw_fallback(self) -> None:
        html_text = "<html><body>unused</body></html>"
        long_body = " ".join(f"word{i}" for i in range(3000))

        with patch("KoreLiveWeb.app.web_fetch._fetch_html", return_value=(html_text, "https://example.com/stats")):
            with patch("KoreLiveWeb.app.web_fetch._extract_content", return_value=("Stats Page", long_body)):
                with patch("KoreLiveWeb.app.web_fetch._get_active_model", return_value="gpt-oss:20b"):
                    with patch("KoreLiveWeb.app.web_fetch._get_active_num_ctx", return_value=131072):
                        with patch(
                            "KoreLiveWeb.app.web_fetch._call_llm_chat",
                            return_value=SimpleNamespace(response="Not found on this page."),
                        ):
                            result = fetch_page_text(
                                url="https://example.com/stats",
                                max_words=400,
                                timeout_seconds=30,
                                query="list all historical winners",
                            )

        body_words = result.split()[3:]
        self.assertEqual(result.split()[0:2], ["#", "Stats"])
        self.assertGreaterEqual(len(body_words), 2500)

    def test_web_evidence_guard_requires_fetch_after_search_for_web_facts_prompt(self) -> None:
        tool_outputs = [
            ToolCallResult(
                tool      = "search_web",
                function  = "search_web",
                module    = "mcp",
                arguments = {"query": "facts about turtles"},
                result    = [{"title": "Turtle facts", "url": "https://example.com", "snippet": "facts"}],
                status    = "ok",
                error     = "",
            )
        ]

        self.assertTrue(_requires_web_evidence_guard("search the web for facts about turtles", tool_outputs))

    def test_web_evidence_guard_allows_final_answer_after_fetch(self) -> None:
        tool_outputs = [
            ToolCallResult(
                tool      = "search_web",
                function  = "search_web",
                module    = "mcp",
                arguments = {"query": "facts about turtles"},
                result    = [{"title": "Turtle facts", "url": "https://example.com", "snippet": "facts"}],
                status    = "ok",
                error     = "",
            ),
            ToolCallResult(
                tool      = "fetch_page_text",
                function  = "fetch_page_text",
                module    = "mcp",
                arguments = {"url": "https://example.com"},
                result    = "Turtles are reptiles with shells.",
                status    = "ok",
                error     = "",
            ),
        ]

        self.assertFalse(_requires_web_evidence_guard("search the web for facts about turtles", tool_outputs))

    def test_load_session_rebuilds_history_from_korechat(self) -> None:
        session_id = "web_1775338532521"
        scratchpad_clear(session_id)

        conversation = {
            "id": 7,
            "thread_summary": "Prior summary",
            "scratchpad": {"topic": "alpha"},
        }
        messages = [
            {"direction": "inbound", "content": "Hi"},
            {"direction": "outbound", "content": "Hello"},
            {"direction": "inbound", "content": "Need status"},
            {"direction": "outbound", "content": "Status is green"},
        ]

        with patch.object(api_module._session_service, "kc_get_conversation_for_session", return_value=conversation):
            with patch.object(api_module._session_service, "kc_get", return_value=messages):
                history = api_module._load_session(session_id)
                session_context = api_module._create_session_context(session_id=session_id, persist_path=None)

        self.assertEqual(
            history.as_list(),
            [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello"},
                {"role": "user", "content": "Need status"},
                {"role": "assistant", "content": "Status is green"},
            ],
        )
        self.assertEqual(len(session_context.get_turns()), 1)
        self.assertEqual(session_context.get_turns()[0]["assistant_response"], "Prior summary")
        self.assertEqual(scratchpad_load("topic", session_id), "alpha")


if __name__ == "__main__":
    unittest.main()
