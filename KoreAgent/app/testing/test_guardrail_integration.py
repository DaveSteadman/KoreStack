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
#   python -m unittest testing.test_guardrail_integration
#   python -m pytest testing/test_guardrail_integration.py -v
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
from testing import test_wrapper as test_wrapper_module
from utils import workspace_utils as workspace_utils_module
from utils.workspace_utils import get_user_data_dir


class GuardrailIntegrationTests(unittest.TestCase):
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

    def test_dataset_get_uses_deterministic_scratchpad_key(self) -> None:
        key = _derive_auto_scratchpad_key(
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

        self.assertIn("page scratchpad keys", system_message)
        self.assertIn("research_page_*", system_message)
        self.assertIn("instead of scratchpad_load on the entire combined research bundle", system_message)

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

        with patch("KoreLiveWeb.app.web_research.search_web", return_value=search_results):
            with patch("KoreLiveWeb.app.web_research._fetch_html", return_value=(html_text, "https://example.com/results")):
                with patch("KoreLiveWeb.app.web_research._extract_content", return_value=("Example Results", body_text)):
                    with patch("KoreLiveWeb.app.web_research._extract_urls_from_html", return_value=[]):
                        with patch("KoreLiveWeb.app.web_research._llm_reextract_evidence", return_value=["Williams won at Imola in 1981 and 1982."]):
                            result = research_traverse("Williams Imola wins", max_pages=1, max_search_results=1)

        self.assertEqual(result["visited_count"], 1)
        self.assertEqual(len(result["best_pages"]), 1)
        self.assertEqual(len(result["page_manifest"]), 1)
        scratchpad_key = result["best_pages"][0]["scratchpad_key"]
        self.assertEqual(scratchpad_key, result["page_manifest"][0]["scratchpad_key"])
        self.assertTrue(scratchpad_key.startswith("research_page_"))
        saved_page = scratchpad_load(scratchpad_key)
        self.assertIn("RESEARCH QUERY: Williams Imola wins", saved_page)
        self.assertIn("TITLE: Example Results", saved_page)
        self.assertIn("URL: https://example.com/results", saved_page)
        self.assertIn("PAGE EXTRACT:", saved_page)
        self.assertIn("Williams won at Imola in 1981 and 1982.", saved_page)
        self.assertIn(f"SCRATCHPAD_KEY: {scratchpad_key}", result["full_report"])
        self.assertNotIn("EXTRACT:", result["full_report"])

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
