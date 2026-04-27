import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
CODE_DIR = REPO_ROOT / "code"

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from KoreAgent.skill_executor import execute_tool_call
from KoreAgent import mcp_client
from KoreAgent.orchestration import _delegate_tls
from KoreAgent.orchestration import delegate_subrun
from KoreAgent.orchestration import OrchestratorConfig
from KoreAgent.prompt_builder import build_system_message
from KoreAgent.scratchpad import scratch_clear
from KoreAgent.scratchpad import get_store
from KoreAgent.scratchpad import scratch_load
from KoreAgent.scratchpad import scratch_query
from KoreAgent.scratchpad import scratch_save
from KoreAgent.session_runtime import bind_session
from KoreAgent.skills_catalog_builder import build_tool_definitions
from KoreAgent.skills_catalog_builder import load_skills_payload
from KoreAgent.system_skills.FileAccess import file_access_skill as file_access_module
from KoreAgent.system_skills.FileAccess.file_access_skill import file_write
from KoreAgent.system_skills.FileAccess.file_access_skill import file_read
from KoreAgent.system_skills.FileAccess.file_access_skill import folder_create
from KoreAgent.skills.WebFetch.web_fetch_skill import fetch_page_text
from KoreAgent.skills.WebSearch.web_search_skill import search_web
from KoreAgent.skills.WebResearch.web_research_skill import research_traverse
from KoreAgent.skills.SystemInfo.system_info_skill import get_system_info_string
from KoreAgent.tool_loop import normalize_tool_request
from KoreAgent.input_layer import api as api_module
from KoreAgent.testing import test_wrapper as test_wrapper_module
from KoreAgent.utils import workspace_utils as workspace_utils_module
from KoreAgent.utils.workspace_utils import get_user_data_dir


class RegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skills_payload = load_skills_payload(CODE_DIR / "KoreAgent" / "skills" / "skills_catalog.json")
        scratch_clear()

    def tearDown(self) -> None:
        scratch_clear()

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
            bootstrap = tmp_root / "default.json"
            bootstrap.parent.mkdir(parents=True, exist_ok=True)
            bootstrap.write_text(
                '{\n'
                '  "ControlDataFolder": "custom_control",\n'
                '  "UserDataFolder": "userdata"\n'
                '}\n',
                encoding="utf-8",
            )

            workspace_utils_module.get_bootstrap_defaults_file.cache_clear()
            workspace_utils_module._load_path_overrides.cache_clear()
            workspace_utils_module.get_controldata_dir.cache_clear()
            workspace_utils_module.get_user_data_dir.cache_clear()

            try:
                with patch.object(workspace_utils_module, "get_workspace_root", return_value=tmp_root):
                    self.assertEqual(workspace_utils_module.get_controldata_dir(), (tmp_root / "custom_control").resolve())
                    self.assertEqual(workspace_utils_module.get_user_data_dir(), (tmp_root / "userdata").resolve())
            finally:
                workspace_utils_module.get_bootstrap_defaults_file.cache_clear()
                workspace_utils_module._load_path_overrides.cache_clear()
                workspace_utils_module.get_controldata_dir.cache_clear()
                workspace_utils_module.get_user_data_dir.cache_clear()

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

        with patch("KoreAgent.skills.WebFetch.web_fetch_skill._fetch_html", return_value=(html_text, "https://www.bbc.co.uk/news")):
            with patch("KoreAgent.skills.WebFetch.web_fetch_skill._extract_content", return_value=("BBC News", body_text)):
                with patch("KoreAgent.skills.WebFetch.web_fetch_skill._get_active_model", return_value="gpt-oss:20b"):
                    with patch("KoreAgent.skills.WebFetch.web_fetch_skill._get_active_num_ctx", return_value=131072):
                        with patch(
                            "KoreAgent.skills.WebFetch.web_fetch_skill._call_llm_chat",
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

        with patch("KoreAgent.skills.WebFetch.web_fetch_skill._fetch_html", return_value=(html_text, "https://example.com/stats")):
            with patch("KoreAgent.skills.WebFetch.web_fetch_skill._extract_content", return_value=("Stats Page", long_body)):
                with patch("KoreAgent.skills.WebFetch.web_fetch_skill._get_active_model", return_value="gpt-oss:20b"):
                    with patch("KoreAgent.skills.WebFetch.web_fetch_skill._get_active_num_ctx", return_value=131072):
                        with patch(
                            "KoreAgent.skills.WebFetch.web_fetch_skill._call_llm_chat",
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

    def test_load_session_rebuilds_history_from_koreconversation(self) -> None:
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

    def test_delete_session_state_deletes_koreconversation_record(self) -> None:
        session_id = "web_1775338532521"
        scratch_save("topic", "alpha", session_id)

        with patch.object(api_module, "_kc_get_conversation_for_session", return_value={"id": 7}):
            with patch.object(api_module, "_kc_delete") as mock_delete:
                api_module._delete_session_state(session_id)

        mock_delete.assert_called_once_with("/conversations/7")
        self.assertEqual(get_store(session_id), {})

    def test_system_prompt_steers_exhaustive_fetches_into_scratchpad(self) -> None:
        system_message = build_system_message("", None, {"skills": []}, skill_guidance_enabled=False, sandbox_enabled=True)

        self.assertIn("complete list, full history, many-year table scan", system_message)
        self.assertIn("auto-saved to the scratchpad", system_message)
        self.assertIn("scratch_query or scratch_peek", system_message)

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
            with patch("KoreAgent.orchestration.orchestrate_prompt", side_effect=fake_orchestrate_prompt):
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

        _delegate_tls.logger = dummy_logger
        _delegate_tls.delegate_depth = 0
        _delegate_tls.config = config

        captured = {}

        def fake_orchestrate_prompt(**kwargs):
            captured["bound_session_id"] = kwargs.get("bound_session_id")
            return ("ok", 0, 0, True, 0.0)

        try:
            with bind_session("parent_session"):
                with patch("KoreAgent.orchestration.orchestrate_prompt", side_effect=fake_orchestrate_prompt):
                    result = delegate_subrun("child task", scratchpad_visible_keys=["saved_key"])
        finally:
            _delegate_tls.logger = previous_logger
            _delegate_tls.delegate_depth = previous_depth
            _delegate_tls.config = previous_config

        self.assertEqual(result["status"], "ok")
        self.assertEqual(captured["bound_session_id"], "parent_session")

    def test_search_web_prefer_article_urls_promotes_article_results(self) -> None:
        html_text = "".join([
            "<a rel='nofollow' href='https://example.com/category/ai' class='result-link'>AI category</a>",
            "<td class='result-snippet'>Hub page for AI coverage.</td>",
            "<a rel='nofollow' href='https://example.com/news/openai-releases-new-model' class='result-link'>OpenAI releases new model</a>",
            "<td class='result-snippet'>Detailed article page.</td>",
        ])

        with patch("KoreAgent.skills.WebSearch.web_search_skill._fetch_html", return_value=(html_text, "https://lite.duckduckgo.com/lite/?q=ai")):
            with patch("KoreAgent.skills.WebSearch.web_search_skill.time.sleep", return_value=None):
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

        with patch("KoreAgent.skills.WebSearch.web_search_skill._fetch_html", return_value=(html_text, "https://lite.duckduckgo.com/lite/?q=ai")):
            with patch("KoreAgent.skills.WebSearch.web_search_skill.time.sleep", return_value=None):
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

        with patch("KoreAgent.skills.WebResearch.web_research_skill.search_web", return_value=search_results):
            with patch("KoreAgent.skills.WebResearch.web_research_skill._fetch_html", return_value=(html_text, "https://example.com/results")):
                with patch("KoreAgent.skills.WebResearch.web_research_skill._extract_content", return_value=("Example Results", body_text)):
                    with patch("KoreAgent.skills.WebResearch.web_research_skill._extract_urls_from_html", return_value=[]):
                        with patch("KoreAgent.skills.WebResearch.web_research_skill._llm_reextract_evidence", return_value=["Williams won at Imola in 1981 and 1982."]):
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

        with patch("KoreAgent.llm_client.call_llm_chat") as llm_call:
            result = scratch_query("search_block", "list all the Williams F1 team wins at Imola")

        self.assertEqual(result, "Not found in content.")
        llm_call.assert_not_called()

    def test_scratch_query_prompt_forbids_outside_knowledge(self) -> None:
        scratch_save("race_rows", "1992 Ayrton Senna\n1993 Ayrton Senna")

        with patch("KoreAgent.llm_client.get_active_model", return_value="gpt-oss:20b"):
            with patch("KoreAgent.llm_client.get_active_num_ctx", return_value=131072):
                with patch(
                    "KoreAgent.llm_client.call_llm_chat",
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
