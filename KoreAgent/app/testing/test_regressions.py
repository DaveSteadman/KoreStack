# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Regression test suite for KoreAgent core modules.
#
# Uses unittest.TestCase to validate key module imports and basic function behaviour:
#   - skill_executor.execute_tool_call dispatch
#   - scratchpad read/write round-trip
#   - file_access skill validation
#   - web tools availability
#   - orchestration helpers (compact_context, assess_compact)
#
# Run via:  python -m pytest testing/test_regressions.py -v
#        or:  /test test_regressions from within KoreAgent chat UI
#
# Related modules:
#   - testing/test_wrapper.py  -- wraps individual test files for /test execution
#   - skill_executor.py        -- execute_tool_call
#   - scratchpad.py            -- scratch_save, scratch_load
# ====================================================================================================
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = Path(__file__).resolve().parents[1]

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import datasets as datasets_module
import tool_loop as tool_loop_module
from conversation_state import decode_background_context
from conversation_state import encode_background_context
from skill_executor import execute_tool_call
import datasets_store
import mcp_client
from orchestration import _delegate_tls
from orchestration import delegate_subrun
from orchestration import OrchestratorConfig
from input_layer import koreconv_input as koreconv_input_module
from datasets import auto_route_tool_result
from datasets import clear_session_datasets
from datasets import dataset_drop_where
from datasets import dataset_expand_full_text
from datasets import dataset_filter
from datasets import dataset_get
from datasets import dataset_inspect
from datasets import dataset_list
from datasets import dataset_rename
from datasets import dataset_save
from datasets import dataset_write_koredoc
from datasets import delete_session_datasets
from datasets import get_persisted_datasets_payload
from datasets import restore_persisted_datasets
from prompt_builder import build_system_message
from scratchpad import scratch_clear
from scratchpad import get_store
from scratchpad import scratch_load
from scratchpad import scratch_query
from scratchpad import scratch_save
from session_runtime import bind_session
from skills_catalog_builder import build_tool_definitions
from skills_catalog_builder import load_skills_payload
from system_skills.FileAccess import file_access_skill as file_access_module
from system_skills.FileAccess.file_access_skill import file_write
from system_skills.FileAccess.file_access_skill import file_read
from system_skills.FileAccess.file_access_skill import folder_create
from skills.WebFetch.web_fetch_skill import fetch_page_text
from skills.WebSearch.web_search_skill import search_web
from skills.WebResearch.web_research_skill import research_traverse
from skills.SystemInfo.system_info_skill import get_system_info_string
from tool_loop import normalize_tool_request
from tool_loop import _derive_auto_scratch_key
from tool_loop import _extract_graph_connection_batch_from_text
from tool_result import ToolCallResult
from input_layer import server as api_module
from input_layer import slash_command_handlers_sessions as session_handlers_module
from input_layer.routes_sessions import _runtime_config_for_prompt
from input_layer.slash_command_handlers_testing import _result_counts
from testing import test_wrapper as test_wrapper_module
from utils import workspace_utils as workspace_utils_module
from utils.workspace_utils import get_user_data_dir


class RegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skills_payload = load_skills_payload(CODE_DIR / "skills" / "skills_catalog.json")
        scratch_clear()
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
        scratch_clear()
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

            with patch.object(file_access_module, "WORKSPACE_ROOT", tmp_root):
                with patch.object(file_access_module, "DEFAULT_DATA_DIR", data_dir):
                    result = file_read("data/ai-sites.json")

        self.assertEqual(result, '{"ok": true}')

    def test_create_folder_accepts_workspace_relative_data_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            data_dir = tmp_root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            with patch.object(file_access_module, "WORKSPACE_ROOT", tmp_root):
                with patch.object(file_access_module, "DEFAULT_DATA_DIR", data_dir):
                    result = folder_create("data/2026-04-05")

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
                '  "ControlDataFolder": "custom_control",\n'
                '  "UserDataFolder": "userdata"\n'
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
                    self.assertEqual(workspace_utils_module.get_controldata_dir(), (tmp_root / "custom_control").resolve())
                    self.assertEqual(workspace_utils_module.get_user_data_dir(), (tmp_root / "userdata").resolve())
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
            config_path = Path(tmp) / "default.json"
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
            config_path = Path(tmp) / "default.json"
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
                    "max_iterations": 3,
                },
            },
        )

        self.assertEqual(func_name, "delegate")
        self.assertIn("prompt", arguments)
        self.assertNotIn("task", arguments)
        self.assertEqual(arguments["prompt"], "Find the latest advancements in quantum computing and provide a concise summary.")
        self.assertEqual(arguments["max_iterations"], 3)
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

        with patch("skills.WebFetch.web_fetch_skill._fetch_html", return_value=(html_text, "https://www.bbc.co.uk/news")):
            with patch("skills.WebFetch.web_fetch_skill._extract_content", return_value=("BBC News", body_text)):
                with patch("skills.WebFetch.web_fetch_skill._get_active_model", return_value="gpt-oss:20b"):
                    with patch("skills.WebFetch.web_fetch_skill._get_active_num_ctx", return_value=131072):
                        with patch(
                            "skills.WebFetch.web_fetch_skill._call_llm_chat",
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

        with patch("skills.WebFetch.web_fetch_skill._fetch_html", return_value=(html_text, "https://example.com/stats")):
            with patch("skills.WebFetch.web_fetch_skill._extract_content", return_value=("Stats Page", long_body)):
                with patch("skills.WebFetch.web_fetch_skill._get_active_model", return_value="gpt-oss:20b"):
                    with patch("skills.WebFetch.web_fetch_skill._get_active_num_ctx", return_value=131072):
                        with patch(
                            "skills.WebFetch.web_fetch_skill._call_llm_chat",
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

    def test_load_session_rebuilds_history_from_korechat(self) -> None:
        session_id = "web_1775338532521"
        scratch_clear(session_id)

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

        with patch.object(api_module, "_kc_get_conversation_for_session", return_value=conversation):
            with patch.object(api_module, "_kc_get", return_value=messages):
                history, summaries = api_module._load_session(session_id)

        self.assertEqual(
            history.as_list(),
            [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello"},
                {"role": "user", "content": "Need status"},
                {"role": "assistant", "content": "Status is green"},
            ],
        )
        self.assertEqual(summaries, [{"text": "Prior summary", "turn_range": [1, 1]}])
        self.assertEqual(scratch_load("topic", session_id), "alpha")

    def test_load_session_restores_datasets_from_korechat_payload(self) -> None:
        session_id = "dataset_load_session"
        dataset_save(
            "feed_items_raw",
            [
                {"title": "Alpha", "url": "https://example.com/a", "source": "Example"},
                {"title": "Beta", "url": "https://example.com/b", "source": "Example"},
            ],
            source_tool="koredata_search",
            source_args={"query": "alpha beta"},
            session_id=session_id,
        )
        datasets_payload = get_persisted_datasets_payload(session_id)

        clear_session_datasets(session_id)
        scratch_clear(session_id)

        conversation = {
            "id": 7,
            "thread_summary": "",
            "scratchpad": {"topic": "alpha"},
            "datasets": datasets_payload,
        }

        with patch.object(api_module, "_kc_get_conversation_for_session", return_value=conversation):
            with patch.object(api_module, "_kc_get", return_value=[]):
                history, summaries = api_module._load_session(session_id)

        self.assertEqual(history.as_list(), [])
        self.assertEqual(summaries, [])
        self.assertEqual(scratch_load("topic", session_id), "alpha")
        manifest = json.loads(dataset_inspect("feed_items_raw", session_id=session_id))
        self.assertEqual(manifest["source_tool"], "koredata_search")
        self.assertEqual(manifest["count"], 2)

    def test_koreconv_prompt_renders_datasets_separately(self) -> None:
        prompt = koreconv_input_module._build_prompt(
            {
                "id": 7,
                "thread_summary": "",
                "background_context": "",
                "scratchpad": {"topic": "alpha"},
                "datasets": {
                    "feed_items_raw": {
                        "dataset_id": "ds_example",
                        "inline": False,
                        "count": 2,
                        "schema": ["title", "url"],
                    }
                },
            },
            [],
        )

        self.assertIn("topic: alpha", prompt)
        self.assertIn("--- Datasets ---", prompt)
        self.assertIn("feed_items_raw: 2 records fields=[title, url]", prompt)
        self.assertNotIn("ds_example", prompt)

    def test_koreconv_event_restores_datasets_before_orchestration(self) -> None:
        session_id = "kc_conv_701"
        dataset_save(
            "feed_items_raw",
            [
                {"title": "Alpha", "url": "https://example.com/a", "source": "Example"},
                {"title": "Beta", "url": "https://example.com/b", "source": "Example"},
            ],
            source_tool="koredata_search",
            session_id=session_id,
        )
        datasets_payload = get_persisted_datasets_payload(session_id)
        clear_session_datasets(session_id)
        scratch_clear(session_id)

        event = {
            "id": 91,
            "event_type": "response_needed",
            "conversation": {
                "id": 701,
                "turn_count": 0,
                "channel_type": "webchat",
                "scratchpad": {},
                "datasets": datasets_payload,
                "messages": [{"direction": "inbound", "content": "list datasets", "summarised": 0}],
            },
        }

        captured = {}
        patched_calls = []

        class _DummyLogger:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class _DummySessionContext:
            def __init__(self) -> None:
                self._lock = unittest.mock.MagicMock()
                self._lock.__enter__ = lambda *_args: None
                self._lock.__exit__ = lambda *_args: False
                self._turns = []

            def get_turns(self):
                return []

        def fake_orchestrate_prompt(**_kwargs):
            captured["datasets"] = dataset_list(session_id=session_id)
            return ("Datasets available.", 10, 5, True, 1.0)

        with patch.object(koreconv_input_module, "_get_base_url", return_value="http://127.0.0.1:9602"):
            with patch.object(koreconv_input_module, "make_task_session", return_value=(None, _DummySessionContext())):
                with patch.object(koreconv_input_module, "orchestrate_prompt", side_effect=fake_orchestrate_prompt):
                    with patch.object(koreconv_input_module, "_http_post", side_effect=lambda *args, **kwargs: patched_calls.append(("post", args, kwargs)) or {}):
                        with patch.object(koreconv_input_module, "_http_patch", side_effect=lambda *args, **kwargs: patched_calls.append(("patch", args, kwargs)) or {}):
                            koreconv_input_module._handle_event(
                                event,
                                OrchestratorConfig(
                                    resolved_model="gpt-oss:20b",
                                    num_ctx=131072,
                                    max_iterations=3,
                                    skills_payload=self.skills_payload,
                                ),
                                Path("."),
                                lambda _path: _DummyLogger(),
                                lambda log_dir: Path(log_dir) / "dummy.log",
                                lambda _line: None,
                            )

        self.assertIn("feed_items_raw", captured["datasets"])
        patch_payloads = [args[2] for kind, args, _kwargs in patched_calls if kind == "patch" and len(args) >= 3]
        self.assertTrue(any("scratchpad" in payload and "datasets" in payload for payload in patch_payloads))

    def test_background_context_round_trip_is_versioned(self) -> None:
        encoded = encode_background_context(
            [
                {
                    "turn": 4,
                    "user_prompt": "Find the latest feed items about batteries.",
                    "assistant_response": "I found three relevant stories.",
                    "skill_outputs": [{"skill": "koredata_search", "summary": "3 results"}],
                }
            ]
        )

        payload = json.loads(encoded)
        restored_turns, warning = decode_background_context(encoded)

        self.assertEqual(payload["version"], 1)
        self.assertIsNone(warning)
        self.assertEqual(restored_turns[0]["turn"], 4)
        self.assertEqual(restored_turns[0]["skill_outputs"][0]["skill"], "koredata_search")

    def test_koreconv_event_marks_failed_when_outbound_write_fails(self) -> None:
        event = {
            "id": 91,
            "event_type": "response_needed",
            "conversation": {
                "id": 701,
                "turn_count": 0,
                "channel_type": "webchat",
                "background_context": "",
                "scratchpad": {},
                "messages": [{"direction": "inbound", "content": "hello", "summarised": 0}],
            },
        }

        posts: list[tuple[str, dict]] = []
        patches: list[tuple[str, dict]] = []

        class _DummyLogger:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class _DummySessionContext:
            def __init__(self) -> None:
                self._lock = unittest.mock.MagicMock()
                self._lock.__enter__ = lambda *_args: None
                self._lock.__exit__ = lambda *_args: False
                self._turns = []

            def get_turns(self):
                return []

        def fake_http_post(_base: str, path: str, payload: dict, timeout: int = 8):
            posts.append((path, payload))
            if path == "/conversations/701/messages":
                raise RuntimeError("write failed")
            return {}

        with patch.object(koreconv_input_module, "_get_base_url", return_value="http://127.0.0.1:9602"):
            with patch.object(koreconv_input_module, "_http_get", return_value=[]):
                with patch.object(koreconv_input_module, "make_task_session", return_value=(None, _DummySessionContext())):
                    with patch.object(koreconv_input_module, "orchestrate_prompt", return_value=("Hi", 10, 5, True, 1.0)):
                        with patch.object(koreconv_input_module, "_http_post", side_effect=fake_http_post):
                            with patch.object(koreconv_input_module, "_http_patch", side_effect=lambda _base, path, payload, timeout=8: patches.append((path, payload)) or {}):
                                koreconv_input_module._handle_event(
                                    event,
                                    OrchestratorConfig(
                                        resolved_model="gpt-oss:20b",
                                        num_ctx=131072,
                                        max_iterations=3,
                                        skills_payload=self.skills_payload,
                                    ),
                                    Path("."),
                                    lambda _path: _DummyLogger(),
                                    lambda log_dir: Path(log_dir) / "dummy.log",
                                    lambda _line: None,
                                )

        self.assertIn(("/events/91/complete", {"status": "failed"}), posts)
        self.assertEqual(patches, [])

    def test_clone_conversation_resets_token_estimate_and_recomputes_turn_count(self) -> None:
        source = {
            "id": 7,
            "channel_type": "webchat",
            "background_context": "ctx",
            "profile": "admin",
            "thread_summary": "summary",
            "scratchpad": {"topic": "alpha"},
            "token_estimate": 999,
            "turn_count": 42,
        }
        source_messages = [
            {"direction": "inbound", "content": "one", "sender_display": "user", "status": "received"},
            {"direction": "outbound", "content": "reply", "sender_display": "agent", "status": "sent"},
            {"direction": "inbound", "content": "two", "sender_display": "user", "status": "received"},
        ]
        patch_calls = []

        def fake_post(path: str, payload: dict):
            if path == "/conversations":
                return {"id": 99}
            return {}

        def fake_get(path: str):
            if path == "/conversations/7/messages?limit=1000":
                return source_messages
            if path == "/conversations/99":
                return {"id": 99}
            return None

        with patch.object(session_handlers_module, "_kc_post", side_effect=fake_post):
            with patch.object(session_handlers_module, "_kc_get", side_effect=fake_get):
                with patch.object(session_handlers_module, "_kc_patch", side_effect=lambda path, payload: patch_calls.append((path, payload)) or {}):
                    session_handlers_module._clone_conversation(source, "Copy", "web_2")

        self.assertEqual(patch_calls[-1][1]["token_estimate"], 0)
        self.assertEqual(patch_calls[-1][1]["turn_count"], 2)

    def test_delete_session_state_deletes_korechat_record(self) -> None:
        session_id = "web_1775338532521"
        scratch_save("topic", "alpha", session_id)

        with patch.object(api_module, "_kc_get_conversation_for_session", return_value={"id": 7}):
            with patch.object(api_module, "_kc_delete") as mock_delete:
                api_module._delete_session_state(session_id)

        mock_delete.assert_called_once_with("/conversations/7")
        self.assertEqual(get_store(session_id), {})

    def test_dataset_rename_preserves_dataset_id(self) -> None:
        session_id = "dataset_test"
        dataset_save(
            "feed_items_raw",
            [
                {"title": "Alpha", "url": "https://example.com/a", "source": "Example"},
                {"title": "Beta", "url": "https://example.com/b", "source": "Example"},
            ],
            session_id=session_id,
        )

        before = json.loads(dataset_inspect("feed_items_raw", session_id=session_id))
        rename_result = dataset_rename("feed_items_raw", "feed_items_relevant", session_id=session_id)
        after = json.loads(dataset_inspect("feed_items_relevant", session_id=session_id))

        self.assertIn("feed_items_relevant", rename_result)
        self.assertEqual(before["dataset_id"], after["dataset_id"])

    def test_dataset_drop_where_forks_by_default(self) -> None:
        session_id = "dataset_test"
        dataset_save(
            "feed_items_raw",
            [
                {"title": "Alpha", "url": "https://example.com/a"},
                {"title": "Alpha duplicate", "url": "https://example.com/a"},
                {"title": "Beta", "url": "https://example.com/b"},
            ],
            session_id=session_id,
        )

        result = dataset_drop_where("feed_items_raw", "duplicate by url", save_as="feed_items_deduped", session_id=session_id)
        original = json.loads(dataset_inspect("feed_items_raw", session_id=session_id))
        deduped = json.loads(dataset_inspect("feed_items_deduped", session_id=session_id))

        self.assertIn("feed_items_deduped", result)
        self.assertEqual(original["count"], 3)
        self.assertEqual(deduped["count"], 2)

    def test_dataset_filter_uses_projected_records(self) -> None:
        session_id = "dataset_filter"
        dataset_save(
            "feed_items_raw",
            [
                {"title": "Renewable energy accelerates", "url": "https://example.com/a", "body": "solar body"},
                {"title": "Football transfer rumours", "url": "https://example.com/b", "body": "sports body"},
            ],
            session_id=session_id,
        )

        def fake_call_llm_chat(**kwargs):
            content = kwargs["messages"][1]["content"]
            if "Renewable energy accelerates" in content:
                return SimpleNamespace(response='{"keep": true, "reason": "topical"}')
            return SimpleNamespace(response='{"keep": false, "reason": "off-topic"}')

        with patch("llm_client.get_active_model", return_value="gpt-oss:20b"):
            with patch("llm_client.get_active_num_ctx", return_value=131072):
                with patch("llm_client.call_llm_chat", side_effect=fake_call_llm_chat):
                    result = dataset_filter(
                        "feed_items_raw",
                        "Keep only items about renewable energy.",
                        save_as="feed_items_relevant",
                        fields=["title", "url"],
                        session_id=session_id,
                    )

        filtered = json.loads(dataset_inspect("feed_items_relevant", session_id=session_id))
        self.assertIn("feed_items_relevant", result)
        self.assertEqual(filtered["count"], 1)

    def test_dataset_persistence_round_trip_handles_spillover(self) -> None:
        session_id = "dataset_restore"
        large_records = [
            {
                "title": f"Article {index}",
                "url": f"https://example.com/{index}",
                "body": "x" * 12000,
            }
            for index in range(5)
        ]
        dataset_save("feed_items_raw", large_records, session_id=session_id)

        payload = get_persisted_datasets_payload(session_id)
        self.assertFalse(payload["feed_items_raw"]["inline"])
        self.assertEqual(payload["feed_items_raw"]["count"], 5)

        clear_session_datasets(session_id)
        restore_persisted_datasets(payload, session_id)

        listed = dataset_get("feed_items_raw", max_records=1, fields=["title", "url"], session_id=session_id)
        self.assertIn("Article 0", listed)

    def test_dataset_reports_missing_spillover_row(self) -> None:
        session_id = "dataset_restore"
        large_records = [
            {"title": f"Article {index}", "url": f"https://example.com/{index}", "body": "x" * 12000}
            for index in range(5)
        ]
        dataset_save("feed_items_raw", large_records, session_id=session_id)
        payload = get_persisted_datasets_payload(session_id)
        dataset_id = payload["feed_items_raw"]["dataset_id"]

        datasets_store.delete_dataset(dataset_id)
        clear_session_datasets(session_id)
        restore_persisted_datasets(payload, session_id)

        result = json.loads(dataset_inspect("feed_items_raw", session_id=session_id))
        self.assertFalse(result["ok"])
        self.assertIn("missing spillover row", result["error"])

    def test_dataset_get_returns_paged_envelope(self) -> None:
        session_id = "dataset_paging"
        dataset_save(
            "feed_items_raw",
            [{"id": index, "title": f"Story {index}", "url": f"https://example.com/{index}"} for index in range(6)],
            session_id=session_id,
        )

        payload = json.loads(dataset_get("feed_items_raw", offset=2, limit=2, fields=["id", "title"], session_id=session_id))

        self.assertEqual(payload["name"], "feed_items_raw")
        self.assertEqual(payload["total_count"], 6)
        self.assertEqual(payload["offset"], 2)
        self.assertEqual(payload["limit"], 2)
        self.assertEqual(payload["returned"], 2)
        self.assertTrue(payload["has_more"])
        self.assertEqual(payload["next_offset"], 4)
        self.assertEqual(payload["records"], [{"id": 2, "title": "Story 2"}, {"id": 3, "title": "Story 3"}])

    def test_dataset_write_koredoc_writes_real_dataset_rows(self) -> None:
        session_id = "dataset_export"
        dataset_save(
            "drone_test_raw_5",
            [
                {"id": 4037, "title": "Real story alpha", "source": "AP", "snippet": "Alpha snippet", "url": "https://example.com/a"},
                {"id": 4463, "title": "Real story beta", "source": "UKDJ", "snippet": "Beta snippet", "url": "https://example.com/b"},
            ],
            source_tool="koredata_search",
            session_id=session_id,
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            data_dir = tmp_root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            with patch.object(file_access_module, "WORKSPACE_ROOT", tmp_root):
                with patch.object(file_access_module, "DEFAULT_DATA_DIR", data_dir):
                    result = dataset_write_koredoc("drone_test_raw_5", "feeds2", session_id=session_id)

            exported = data_dir / "KoreDocs" / "feeds2" / "drone_test_raw_5.koredoc"
            self.assertTrue(exported.exists())
            content = exported.read_text(encoding="utf-8")

        self.assertIn("Exported dataset 'drone_test_raw_5' records 1-2 of 2", result)
        self.assertIn("KoreDocs document 'drone_test_raw_5.koredoc'", result)
        self.assertIn("at 'KoreDocs/feeds2/drone_test_raw_5.koredoc'", result)
        self.assertNotIn("Wrote ", result)
        self.assertIn("Real story alpha", content)
        self.assertIn("Real story beta", content)
        self.assertNotIn("sample snippet", content.lower())

    def test_dataset_expand_full_text_creates_enriched_dataset(self) -> None:
        session_id = "dataset_fulltext"
        dataset_save(
            "drone_test_raw_6",
            [
                {
                    "artifact_ref": "feed_entry|domain=tech|id=42",
                    "title": "Drone story",
                    "snippet": "preview",
                },
                {
                    "artifact_ref": "reference_article|title=Drone%20warfare",
                    "title": "Drone warfare",
                    "snippet": "preview",
                },
                {
                    "title": "Missing ref row",
                    "snippet": "preview",
                },
            ],
            source_tool="koredata_search",
            source_args={"query": "drones", "domains": ["feeds", "reference"]},
            session_id=session_id,
        )

        def fake_fetch(refid: str, *, client=None, base_url: str = "") -> dict:
            if refid == "feed_entry|domain=tech|id=42":
                return {"page_text": "Full feed body", "domain": "tech", "id": 42}
            if refid == "reference_article|title=Drone%20warfare":
                return {"body": "Full reference body", "title": "Drone warfare"}
            return {"error": "not found"}

        with patch.object(datasets_module, "_get_koredata_gateway_base_url", return_value="http://127.0.0.1:9603"):
            with patch.object(datasets_module, "_fetch_full_text_payload", side_effect=fake_fetch):
                result = dataset_expand_full_text("drone_test_raw_6", save_as="drone_test_fulltext", session_id=session_id)

        self.assertIn("Created dataset 'drone_test_fulltext'", result)
        self.assertIn("expanded 2/3 selected records", result)
        self.assertIn("Skipped 1 record", result)

        expanded_payload = json.loads(dataset_get("drone_test_fulltext", session_id=session_id))
        self.assertEqual(expanded_payload["total_count"], 2)
        self.assertEqual(expanded_payload["records"][0]["artifact_ref"], "feed_entry|domain=tech|id=42")
        self.assertEqual(expanded_payload["records"][0]["page_text"], "Full feed body")
        self.assertEqual(expanded_payload["records"][1]["body"], "Full reference body")

        manifest = json.loads(dataset_inspect("drone_test_fulltext", session_id=session_id))
        self.assertEqual(manifest["source_tool"], "dataset_expand_full_text")

    def test_file_write_blocks_suspicious_placeholder_koredoc_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            data_dir = tmp_root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            with patch.object(file_access_module, "WORKSPACE_ROOT", tmp_root):
                with patch.object(file_access_module, "DEFAULT_DATA_DIR", data_dir):
                    result = file_write(
                        "KoreDocs/feeds2/drone_test_raw_5.koredoc",
                        "### Record 1\n- **Snippet:** This is a sample snippet for entry 1.\n",
                    )

        self.assertIn("refusing to write suspicious placeholder content", result)

    def test_dataset_get_uses_deterministic_scratch_key(self) -> None:
        key = _derive_auto_scratch_key(
            "dataset_get",
            {"name": "drone_test_raw_5", "offset": 20, "limit": 10, "fields": ["id", "title"]},
            7,
            1,
        )

        self.assertEqual(key, "_dataset_get_drone_test_raw_5_o20_l10_fid_title")

    def test_system_prompt_lists_dataset_manifests(self) -> None:
        session_id = "dataset_prompt"
        dataset_save(
            "feed_items_raw",
            [
                {"title": "Alpha", "url": "https://example.com/a", "source": "Example"},
                {"title": "Beta", "url": "https://example.com/b", "source": "Example"},
            ],
            source_tool="koredata_search",
            session_id=session_id,
        )

        with bind_session(session_id):
            system_message = build_system_message("", None, self.skills_payload, skill_guidance_enabled=False, sandbox_enabled=True)

        self.assertIn("Datasets currently stored", system_message)
        self.assertIn("feed_items_raw", system_message)
        self.assertIn("source=koredata_search", system_message)

    def test_system_prompt_hides_dataset_manifests_without_dataset_tools(self) -> None:
        session_id = "dataset_prompt"
        dataset_save(
            "feed_items_raw",
            [{"title": "Alpha", "url": "https://example.com/a", "source": "Example"}],
            source_tool="koredata_search",
            session_id=session_id,
        )

        with bind_session(session_id):
            system_message = build_system_message("", None, {"skills": []}, skill_guidance_enabled=False, sandbox_enabled=True)

        self.assertNotIn("Datasets currently stored", system_message)

    def test_system_prompt_includes_korechat_conversation_snapshot(self) -> None:
        system_message = build_system_message(
            "",
            None,
            {"skills": []},
            skill_guidance_enabled=False,
            sandbox_enabled=True,
            conversation_entry={
                "id": 7,
                "channel_type": "webchat",
                "subject": "Delegate parent",
                "background_context": "prior turn context goes here",
                "scratchpad": {"topic": "alpha"},
                "datasets": {
                    "feed_items_raw": {
                        "dataset_id": "ds_7",
                        "inline": False,
                        "count": 2,
                        "schema": ["title", "url"],
                    }
                },
                "messages": [{"direction": "inbound", "content": "Hello"}],
            },
        )

        self.assertIn("Active KoreChat conversation entry", system_message)
        self.assertIn('"subject": "Delegate parent"', system_message)
        self.assertIn('"datasets": {', system_message)
        self.assertIn('"feed_items_raw"', system_message)
        self.assertIn('"names": [', system_message)
        self.assertIn('"messages": {', system_message)
        self.assertIn('"count": 1', system_message)

    def test_auto_route_tool_result_saves_record_collections_as_dataset(self) -> None:
        session_id = "dataset_auto"
        manifest = auto_route_tool_result(
            "koredata_search",
            {"query": "renewable energy", "domains": ["feeds"]},
            [
                {"title": f"Story {index}", "url": f"https://example.com/{index}", "source": "Example"}
                for index in range(5)
            ],
            session_id=session_id,
        )

        self.assertIsNotNone(manifest)
        self.assertIn("Dataset 'koredata_search_1' created", manifest)
        self.assertIn("dataset_rename", manifest)
        self.assertIn("koredata_search_1", dataset_list(session_id=session_id))

    def test_auto_route_tool_result_parses_stringified_json_results(self) -> None:
        session_id = "dataset_auto_json"
        payload = json.dumps({
            "query": "drones",
            "results": [
                {"title": f"Story {index}", "url": f"https://example.com/{index}", "source": "Example"}
                for index in range(5)
            ],
        })

        manifest = auto_route_tool_result(
            "koredata_search",
            {"query": "drones", "domains": ["feeds"]},
            payload,
            session_id=session_id,
        )

        self.assertIsNotNone(manifest)
        self.assertIn("5 records", manifest)
        self.assertIn("koredata_search_1", dataset_list(session_id=session_id))

    def test_auto_route_tool_result_skips_dataset_get_payloads(self) -> None:
        session_id = "dataset_auto_skip_get"
        payload = json.dumps([
            {"id": index, "title": f"Story {index}", "url": f"https://example.com/{index}"}
            for index in range(5)
        ])

        manifest = auto_route_tool_result(
            "dataset_get",
            {"name": "feed_items_raw", "max_records": 5},
            payload,
            session_id=session_id,
        )

        self.assertIsNone(manifest)
        self.assertEqual(dataset_list(session_id=session_id), "No datasets stored.")

    def test_dataset_save_accepts_results_envelope_dict(self) -> None:
        session_id = "dataset_save_envelope"
        message = dataset_save(
            "feed_items_raw",
            {
                "query": "drones",
                "results": [
                    {"title": "Story 1", "url": "https://example.com/1", "source": "Example"},
                    {"title": "Story 2", "url": "https://example.com/2", "source": "Example"},
                ],
            },
            source_tool="koredata_search",
            source_args={"query": "drones", "domains": ["feeds"]},
            session_id=session_id,
        )

        self.assertIn("Saved dataset 'feed_items_raw' (2 records", message)

    def test_system_prompt_steers_exhaustive_fetches_into_scratchpad(self) -> None:
        system_message = build_system_message("", None, {"skills": []}, skill_guidance_enabled=False, sandbox_enabled=True)

        self.assertIn("The scratchpad tool can store intermediate results across steps.", system_message)
        self.assertIn("do not rebuild full records from the visible preview", system_message)
        self.assertIn("Do not turn dataset_get output into a new dataset summary", system_message)
        self.assertIn("prefer dataset_write_koredoc", system_message)
        self.assertIn("treat that as a KoreDocs destination", system_message)
        self.assertIn("prefer koredata_get_full_text(refid)", system_message)
        self.assertIn("prefer dataset_expand_full_text", system_message)
        self.assertIn("For KoreDocs outputs, prefer dataset_write_koredoc", system_message)

    def test_system_prompt_steers_research_traverse_to_page_keys(self) -> None:
        system_message = build_system_message("", None, {"skills": []}, skill_guidance_enabled=False, sandbox_enabled=True)

        self.assertIn("page scratch keys", system_message)
        self.assertIn("research_page_*", system_message)
        self.assertIn("instead of scratch_load on the entire combined research bundle", system_message)

    def test_system_prompt_steers_article_harvests_away_from_hub_urls(self) -> None:
        system_message = build_system_message("", None, {"skills": []}, skill_guidance_enabled=False, sandbox_enabled=True)

        self.assertIn("concrete article/detail pages", system_message)
        self.assertIn("Do not count homepages, category pages, topic pages, search-result pages, or section fronts", system_message)
        self.assertIn("use get_page_links or get_page_links_text", system_message)
        self.assertIn("prefer_article_urls=true", system_message)

    def test_delegate_subrun_restores_parent_depth_between_siblings(self) -> None:
        dummy_logger = SimpleNamespace(log_file_only=lambda *_args, **_kwargs: None)
        config = OrchestratorConfig(
            resolved_model="gpt-oss:20b",
            num_ctx=131072,
            max_iterations=3,
            skills_payload=self.skills_payload,
        )

        previous_logger = getattr(_delegate_tls, "logger", None)
        previous_depth = getattr(_delegate_tls, "delegate_depth", 0)
        previous_config = getattr(_delegate_tls, "config", None)

        _delegate_tls.logger = dummy_logger
        _delegate_tls.delegate_depth = 0
        _delegate_tls.config = config

        def fake_orchestrate_prompt(**kwargs):
            _delegate_tls.logger = dummy_logger
            _delegate_tls.delegate_depth = kwargs["delegate_depth"]
            _delegate_tls.config = config
            return ("ok", 0, 0, True, 0.0)

        try:
            with patch("orchestration.orchestrate_prompt", side_effect=fake_orchestrate_prompt):
                first = delegate_subrun("first child")
                second = delegate_subrun("second child")
        finally:
            _delegate_tls.logger = previous_logger
            _delegate_tls.delegate_depth = previous_depth
            _delegate_tls.config = previous_config

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")
        self.assertEqual(first["depth"], 1)
        self.assertEqual(second["depth"], 1)

    def test_delegate_subrun_binds_child_to_parent_session(self) -> None:
        dummy_logger = SimpleNamespace(log_file_only=lambda *_args, **_kwargs: None)
        config = OrchestratorConfig(
            resolved_model="gpt-oss:20b",
            num_ctx=131072,
            max_iterations=3,
            skills_payload=self.skills_payload,
        )

        previous_logger = getattr(_delegate_tls, "logger", None)
        previous_depth = getattr(_delegate_tls, "delegate_depth", 0)
        previous_config = getattr(_delegate_tls, "config", None)
        previous_conversation_entry = getattr(_delegate_tls, "conversation_entry", None)

        _delegate_tls.logger = dummy_logger
        _delegate_tls.delegate_depth = 0
        _delegate_tls.config = config
        _delegate_tls.conversation_entry = {"id": 7, "subject": "Parent conversation"}

        captured = {}

        def fake_orchestrate_prompt(**kwargs):
            captured["bound_session_id"] = kwargs.get("bound_session_id")
            captured["conversation_entry"] = kwargs.get("conversation_entry")
            return ("ok", 0, 0, True, 0.0)

        try:
            with bind_session("parent_session"):
                with patch("orchestration.orchestrate_prompt", side_effect=fake_orchestrate_prompt):
                    result = delegate_subrun("child task", scratchpad_visible_keys=["saved_key"])
        finally:
            _delegate_tls.logger = previous_logger
            _delegate_tls.delegate_depth = previous_depth
            _delegate_tls.config = previous_config
            _delegate_tls.conversation_entry = previous_conversation_entry

        self.assertEqual(result["status"], "ok")
        self.assertEqual(captured["bound_session_id"], "parent_session")
        self.assertEqual(captured["conversation_entry"], {"id": 7, "subject": "Parent conversation"})

    def test_delegate_subrun_auto_includes_dataset_access_for_named_dataset_tasks(self) -> None:
        dummy_logger = SimpleNamespace(log_file_only=lambda *_args, **_kwargs: None)
        config = OrchestratorConfig(
            resolved_model="gpt-oss:20b",
            num_ctx=131072,
            max_iterations=3,
            skills_payload=self.skills_payload,
        )

        previous_logger = getattr(_delegate_tls, "logger", None)
        previous_depth = getattr(_delegate_tls, "delegate_depth", 0)
        previous_config = getattr(_delegate_tls, "config", None)

        _delegate_tls.logger = dummy_logger
        _delegate_tls.delegate_depth = 0
        _delegate_tls.config = config

        captured = {}
        session_id = "dataset_prompt"
        dataset_save(
            "drone_test_raw_7",
            [{"artifact_ref": "feed_entry|domain=world|id=1", "title": "Story", "source": "Example"}],
            source_tool="koredata_search",
            session_id=session_id,
        )
        scratch_save("_dataset_get_drone_test_raw_7_o0_l25", "cached rows", session_id=session_id)

        def fake_orchestrate_prompt(**kwargs):
            captured["child_functions"] = {
                fn.split("(", 1)[0].strip()
                for skill in kwargs["config"].skills_payload.get("skills", [])
                for fn in skill.get("functions", [])
            }
            captured["scratchpad_visible_keys"] = list(kwargs.get("scratchpad_visible_keys") or [])
            return ("ok", 0, 0, True, 0.0)

        try:
            with bind_session(session_id):
                with patch("orchestration.orchestrate_prompt", side_effect=fake_orchestrate_prompt):
                    result = delegate_subrun(
                        "Process dataset drone_test_raw_7 and fetch each article body.",
                        tools_allowlist=["koredata_get_full_text"],
                        scratchpad_visible_keys=[],
                    )
        finally:
            _delegate_tls.logger = previous_logger
            _delegate_tls.delegate_depth = previous_depth
            _delegate_tls.config = previous_config

        self.assertEqual(result["status"], "ok")
        self.assertIn("dataset_get", captured["child_functions"])
        self.assertIn("dataset_inspect", captured["child_functions"])
        self.assertIn("_dataset_get_drone_test_raw_7_o0_l25", captured["scratchpad_visible_keys"])

    def test_search_web_prefer_article_urls_promotes_article_results(self) -> None:
        html_text = "".join([
            "<a rel='nofollow' href='https://example.com/category/ai' class='result-link'>AI category</a>",
            "<td class='result-snippet'>Hub page for AI coverage.</td>",
            "<a rel='nofollow' href='https://example.com/news/openai-releases-new-model' class='result-link'>OpenAI releases new model</a>",
            "<td class='result-snippet'>Detailed article page.</td>",
        ])

        with patch("skills.WebSearch.web_search_skill._fetch_html", return_value=(html_text, "https://lite.duckduckgo.com/lite/?q=ai")):
            with patch("skills.WebSearch.web_search_skill.time.sleep", return_value=None):
                default_results = search_web(query="recent AI news", max_results=2, timeout_seconds=10)
                article_results = search_web(query="recent AI news", max_results=2, timeout_seconds=10, prefer_article_urls=True)

        self.assertEqual(default_results[0]["page_kind"], "hub")
        self.assertEqual(default_results[1]["page_kind"], "article")
        self.assertEqual(article_results[0]["page_kind"], "article")
        self.assertEqual(article_results[1]["page_kind"], "hub")

    def test_search_web_extracts_results_when_ddg_attributes_are_reordered(self) -> None:
        html_text = "".join([
            '<a class="result-link result-link--body" rel="nofollow" href="https://example.com/news/openai-releases-new-model">OpenAI releases new model</a>',
            '<td class="result-snippet">Detailed article page.</td>',
            '<a data-testid="organic" href="https://example.com/category/ai" class="result-link extra">AI category</a>',
            '<td class="result-snippet">Hub page for AI coverage.</td>',
        ])

        with patch("skills.WebSearch.web_search_skill._fetch_html", return_value=(html_text, "https://lite.duckduckgo.com/lite/?q=ai")):
            with patch("skills.WebSearch.web_search_skill.time.sleep", return_value=None):
                results = search_web(query="recent AI news", max_results=2, timeout_seconds=10)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "OpenAI releases new model")
        self.assertEqual(results[0]["page_kind"], "article")
        self.assertEqual(results[1]["title"], "AI category")
        self.assertEqual(results[1]["page_kind"], "hub")

    def test_research_traverse_saves_page_level_scratchpad_artifacts(self) -> None:
        search_results = [
            {
                "rank": 1,
                "title": "Example results page",
                "url": "https://example.com/results",
                "snippet": "Detailed results page.",
            }
        ]
        html_text = "<html><body><p>unused</p></body></html>"
        body_text = "Williams won at Imola in 1981 and 1982."

        with patch("skills.WebResearch.web_research_skill.search_web", return_value=search_results):
            with patch("skills.WebResearch.web_research_skill._fetch_html", return_value=(html_text, "https://example.com/results")):
                with patch("skills.WebResearch.web_research_skill._extract_content", return_value=("Example Results", body_text)):
                    with patch("skills.WebResearch.web_research_skill._extract_urls_from_html", return_value=[]):
                        with patch("skills.WebResearch.web_research_skill._llm_reextract_evidence", return_value=["Williams won at Imola in 1981 and 1982."]):
                            result = research_traverse("Williams Imola wins", max_pages=1, max_search_results=1)

        self.assertEqual(result["visited_count"], 1)
        self.assertEqual(len(result["best_pages"]), 1)
        self.assertEqual(len(result["page_manifest"]), 1)
        scratch_key = result["best_pages"][0]["scratch_key"]
        self.assertEqual(scratch_key, result["page_manifest"][0]["scratch_key"])
        self.assertTrue(scratch_key.startswith("research_page_"))
        saved_page = scratch_load(scratch_key)
        self.assertIn("RESEARCH QUERY: Williams Imola wins", saved_page)
        self.assertIn("TITLE: Example Results", saved_page)
        self.assertIn("URL: https://example.com/results", saved_page)
        self.assertIn("PAGE EXTRACT:", saved_page)
        self.assertIn("Williams won at Imola in 1981 and 1982.", saved_page)
        self.assertIn(f"SCRATCH_KEY: {scratch_key}", result["full_report"])
        self.assertNotIn("EXTRACT:", result["full_report"])

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

    def test_scratch_query_rejects_exhaustive_answers_from_search_results(self) -> None:
        search_results = (
            "Web search results for: Williams F1 wins at Imola\n\n"
            "[1] Imola - Wins - Stats F1\n"
            "    https://www.statsf1.com/en/circuit-imola/stats-victoire.aspx\n"
            "    Wins, pole positions, fastest laps, podiums, points.\n\n"
            "[2] Williams at Imola - Lights Out\n"
            "    https://www.lightsoutblog.com/f1-team-form-imola/\n"
            "    Williams scored in all of the last six San Marino Grands Prix.\n"
        )
        scratch_save("search_block", search_results)

        with patch("llm_client.call_llm_chat") as llm_call:
            result = scratch_query("search_block", "list all the Williams F1 team wins at Imola")

        self.assertEqual(result, "Not found in content.")
        llm_call.assert_not_called()

    def test_scratch_query_prompt_forbids_outside_knowledge(self) -> None:
        scratch_save("race_rows", "1992 Ayrton Senna\n1993 Ayrton Senna")

        with patch("llm_client.get_active_model", return_value="gpt-oss:20b"):
            with patch("llm_client.get_active_num_ctx", return_value=131072):
                with patch(
                    "llm_client.call_llm_chat",
                    return_value=SimpleNamespace(response="1992 Ayrton Senna\n1993 Ayrton Senna"),
                ) as llm_call:
                    result = scratch_query("race_rows", "list all rows")

        self.assertEqual(result, "1992 Ayrton Senna\n1993 Ayrton Senna")
        system_prompt = llm_call.call_args.kwargs["messages"][0]["content"]
        self.assertIn("never use outside knowledge", system_prompt)
        self.assertIn("Search result snippets, headlines, and summaries are not authoritative", system_prompt)
        self.assertIn("respond with exactly: Not found in content.", system_prompt)

if __name__ == "__main__":
    unittest.main()
