# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Internal integration guardrail test suite for KoreAgent core modules.
#
# Uses unittest.TestCase to validate key module imports and basic function behaviour:
#   - skill_executor.execute_tool_call dispatch
#   - scratchpad read/write round-trip
#   - file_access skill validation
#   - web tools availability
#   - orchestration helpers (compact_context, assess_compact)
#
# Run manually via:
#   python -m unittest testing.unit.test_guardrail_integration
#   python -m pytest testing/test_guardrail_integration.py -v
#
# The /test slash flow runs prompt suites through testing/test_wrapper.py and then
# executes focused smoke checks from test_guardrail_smoke.py.
#
# Related modules:
#   - testing/test_wrapper.py  -- wraps individual test files for /test execution
#   - skill_executor.py        -- execute_tool_call
#   - scratchpad.py            -- scratchpad_save, scratchpad_load
# MARK: FUNCTIONS
# Primary types: GuardrailIntegrationTests.
# Function inventory:
# - setUp: Implements the setUp operation for this module.
# - tearDown: Implements the tearDown operation for this module.
# - test_dataset_get_uses_deterministic_scratchpad_key: Implements the test dataset get uses deterministic scratchpad key operation for this module.
# - test_system_prompt_lists_dataset_manifests: Implements the test system prompt lists dataset manifests operation for this module.
# - test_system_prompt_hides_dataset_manifests_without_dataset_tools: Implements the test system prompt hides dataset manifests without dataset tools operation for this module.
# - test_system_prompt_includes_korechat_conversation_snapshot: Implements the test system prompt includes korechat conversation snapshot operation for this module.
# - test_auto_route_tool_result_saves_record_collections_as_dataset: Implements the test auto route tool result saves record collections as dataset operation for this module.
# - test_auto_route_tool_result_parses_stringified_json_results: Implements the test auto route tool result parses stringified json results operation for this module.
# - test_auto_route_tool_result_skips_dataset_get_payloads: Implements the test auto route tool result skips dataset get payloads operation for this module.
# - test_dataset_save_accepts_results_envelope_dict: Implements the test dataset save accepts results envelope dict operation for this module.
# - test_system_prompt_steers_exhaustive_fetches_into_scratchpad: Implements the test system prompt steers exhaustive fetches into scratchpad operation for this module.
# - test_system_prompt_steers_article_harvests_away_from_hub_urls: Implements the test system prompt steers article harvests away from hub urls operation for this module.
# - removed_delegate_subrun_restores_parent_depth_between_siblings: Implements the removed delegate subrun restores parent depth between siblings operation for this module.
# - fake_orchestrate_prompt: Implements the fake orchestrate prompt operation for this module.
# - removed_delegate_subrun_binds_child_to_parent_session: Implements the removed delegate subrun binds child to parent session operation for this module.
# - removed_delegate_subrun_auto_includes_dataset_access_for_named_dataset_tasks: Implements the removed delegate subrun auto includes dataset access for named dataset tasks operation for this module.
# - test_search_web_prefer_article_urls_promotes_article_results: Implements the test search web prefer article urls promotes article results operation for this module.
# - test_search_web_extracts_results_when_ddg_attributes_are_reordered: Implements the test search web extracts results when ddg attributes are reordered operation for this module.
# - test_scratchpad_query_rejects_exhaustive_answers_from_search_results: Implements the test scratchpad query rejects exhaustive answers from search results operation for this module.
# - test_scratchpad_query_prompt_forbids_outside_knowledge: Implements the test scratchpad query prompt forbids outside knowledge operation for this module.
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

import system_skills.WorkingData.collections as datasets_module
from agent.tool_runtime import loop as tool_loop_module
from sessions import tool_selection as tool_selection_state_module
from conversation_state import decode_background_context
from conversation_state import encode_background_context
from skill_executor import execute_tool_call
from system_skills.WorkingData.collections import store as datasets_store
from agent.orchestration.engine import ConversationHistory
from agent.orchestration.engine import OrchestratorConfig
from agent.orchestration.engine import orchestrate_prompt
from input_layer import koreconv_input as koreconv_input_module
from system_skills.WorkingData.collections import auto_route_tool_result, clear_session_datasets, dataset_drop_where, dataset_expand_full_text, dataset_filter, dataset_get, dataset_inspect, dataset_list, dataset_rename, dataset_save, dataset_write_koredoc, delete_session_datasets, get_persisted_datasets_payload, restore_persisted_datasets
from prompt_builder import build_system_message
from working_data import working_data_clear as scratchpad_clear, get_working_data_values as get_store, working_data_get as scratchpad_load, working_data_list as scratchpad_list, working_data_query as scratchpad_query, working_data_save as scratchpad_save
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
from agent.tool_runtime.loop import _derive_auto_working_data_key
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


class GuardrailIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skills_payload = load_test_skills_payload(CODE_DIR)
        reset_guardrail_state()

    def tearDown(self) -> None:
        reset_guardrail_state()

    def test_dataset_get_uses_deterministic_scratchpad_key(self) -> None:
        key = _derive_auto_working_data_key(
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
                "subject": "Parent conversation",
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
        self.assertIn('"subject": "Parent conversation"', system_message)
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

    def test_system_prompt_steers_article_harvests_away_from_hub_urls(self) -> None:
        system_message = build_system_message("", None, {"skills": []}, skill_guidance_enabled=False, sandbox_enabled=True)

        self.assertIn("concrete article/detail pages", system_message)
        self.assertIn("Do not count homepages, category pages, topic pages, search-result pages, or section fronts", system_message)
        self.assertIn("use get_page_links or get_page_links_text", system_message)
        self.assertIn("prefer_article_urls=true", system_message)

    def removed_delegate_subrun_restores_parent_depth_between_siblings(self) -> None:
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

    def removed_delegate_subrun_binds_child_to_parent_session(self) -> None:
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

    def removed_delegate_subrun_auto_includes_dataset_access_for_named_dataset_tasks(self) -> None:
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
        scratchpad_save("_dataset_get_drone_test_raw_7_o0_l25", "cached rows", session_id=session_id)

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

        with patch("KoreLiveWeb.app.web_search._fetch_html", return_value=(html_text, "https://lite.duckduckgo.com/lite/?q=ai")):
            with patch("KoreLiveWeb.app.web_search.time.sleep", return_value=None):
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

        with patch("KoreLiveWeb.app.web_search._fetch_html", return_value=(html_text, "https://lite.duckduckgo.com/lite/?q=ai")):
            with patch("KoreLiveWeb.app.web_search.time.sleep", return_value=None):
                results = search_web(query="recent AI news", max_results=2, timeout_seconds=10)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "OpenAI releases new model")
        self.assertEqual(results[0]["page_kind"], "article")
        self.assertEqual(results[1]["title"], "AI category")
        self.assertEqual(results[1]["page_kind"], "hub")

    def test_scratchpad_query_rejects_exhaustive_answers_from_search_results(self) -> None:
        search_results = (
            "Web search results for: Williams F1 wins at Imola\n\n"
            "[1] Imola - Wins - Stats F1\n"
            "    https://www.statsf1.com/en/circuit-imola/stats-victoire.aspx\n"
            "    Wins, pole positions, fastest laps, podiums, points.\n\n"
            "[2] Williams at Imola - Lights Out\n"
            "    https://www.lightsoutblog.com/f1-team-form-imola/\n"
            "    Williams scored in all of the last six San Marino Grands Prix.\n"
        )
        scratchpad_save("search_block", search_results)

        with patch("llm_client.call_llm_chat") as llm_call:
            result = scratchpad_query("search_block", "list all the Williams F1 team wins at Imola")

        self.assertEqual(result, "Not found in content.")
        llm_call.assert_not_called()

    def test_scratchpad_query_prompt_forbids_outside_knowledge(self) -> None:
        scratchpad_save("race_rows", "1992 Ayrton Senna\n1993 Ayrton Senna")

        with patch("llm_client.get_active_model", return_value="gpt-oss:20b"):
            with patch("llm_client.get_active_num_ctx", return_value=131072):
                with patch(
                    "llm_client.call_llm_chat",
                    return_value=SimpleNamespace(response="1992 Ayrton Senna\n1993 Ayrton Senna"),
                ) as llm_call:
                    result = scratchpad_query("race_rows", "list all rows")

        self.assertEqual(result, "1992 Ayrton Senna\n1993 Ayrton Senna")
        system_prompt = llm_call.call_args.kwargs["messages"][0]["content"]
        self.assertIn("never use outside knowledge", system_prompt)
        self.assertIn("Search result snippets, headlines, and summaries are not authoritative", system_prompt)
        self.assertIn("respond with exactly: Not found in content.", system_prompt)



if __name__ == "__main__":
    unittest.main()
