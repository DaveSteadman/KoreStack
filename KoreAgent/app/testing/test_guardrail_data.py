# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Internal data guardrail test suite for KoreAgent core modules.
#
# Uses unittest.TestCase to validate key module imports and basic function behaviour:
#   - skill_executor.execute_tool_call dispatch
#   - scratchpad read/write round-trip
#   - file_access skill validation
#   - web tools availability
#   - orchestration helpers (compact_context, assess_compact)
#
# Run manually via:
#   python -m unittest testing.test_guardrail_data
#   python -m pytest testing/test_guardrail_data.py -v
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

import datasets as datasets_module
import tool_loop as tool_loop_module
import tool_selection_state as tool_selection_state_module
import task_planning as task_planning_module
from conversation_state import decode_background_context
from conversation_state import encode_background_context
from skill_executor import execute_tool_call
import datasets_store
import mcp_client
from orchestration import ConversationHistory
from orchestration import _delegate_tls
from orchestration import delegate_subrun
from orchestration import OrchestratorConfig
from orchestration import orchestrate_prompt
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
from scratchpad import scratchpad_clear
from scratchpad import get_store
from scratchpad import scratchpad_load
from scratchpad import scratchpad_list
from scratchpad import scratchpad_query
from scratchpad import scratchpad_save
from session_runtime import get_active_session_id
from session_runtime import bind_session
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
from tool_loop import normalize_tool_request
from tool_loop import _derive_auto_scratchpad_key
from tool_loop import _extract_graph_connection_batch_from_text
from tool_result import ToolCallResult
from input_layer import server as api_module
from input_layer import slash_commands as slash_commands_module
from input_layer import slash_command_handlers_sessions as session_handlers_module
from input_layer.routes_sessions import _queue_timeout_for_prompt
from input_layer.routes_sessions import _runtime_config_for_prompt
from input_layer.slash_command_handlers_testing import _result_counts
from testing import test_wrapper as test_wrapper_module
from utils import workspace_utils as workspace_utils_module
from utils.workspace_utils import get_user_data_dir


class GuardrailDataTests(unittest.TestCase):
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
        scratchpad_clear(session_id)

        conversation = {
            "id": 7,
            "thread_summary": "",
            "scratchpad": {"topic": "alpha"},
            "datasets": datasets_payload,
        }

        with patch.object(api_module._session_service, "kc_get_conversation_for_session", return_value=conversation):
            with patch.object(api_module._session_service, "kc_get", return_value=[]):
                history = api_module._load_session(session_id)

        self.assertEqual(history.as_list(), [])
        self.assertEqual(scratchpad_load("topic", session_id), "alpha")
        manifest = json.loads(dataset_inspect("feed_items_raw", session_id=session_id))
        self.assertEqual(manifest["source_tool"], "koredata_search")
        self.assertEqual(manifest["count"], 2)

    def test_save_session_promotes_named_items_and_persists_background_context(self) -> None:
        session_id = "web_named_memory"
        scratchpad_clear(session_id)

        history = ConversationHistory()
        history.add(
            "Remember this: my favourite colour is cobalt blue.",
            "Noted.",
        )

        conversation = {"id": 77, "background_context": ""}
        patched_payloads = []

        with patch.object(api_module._session_service, "kc_get_conversation_for_session", return_value=conversation):
            with patch.object(api_module._session_service, "kc_patch", side_effect=lambda path, payload: patched_payloads.append((path, payload)) or conversation):
                session_context = api_module._create_session_context(session_id=session_id, persist_path=None)
                api_module._save_session(session_id, history, session_context, 1000, 1024)

        self.assertEqual(scratchpad_load("memory_favourite_colour", session_id), "cobalt blue")
        self.assertTrue(any("background_context" in payload for _path, payload in patched_payloads))

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
        scratchpad_clear(session_id)

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
        scratchpad_save("topic", "alpha", session_id)

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

            with patch.dict(os.environ, {"KORE_SUITE_ROOT": str(tmp_root), "KORE_SUITE_DATAUSER": str(data_dir)}):
                datauser_fs_module.get_suite_root.cache_clear()
                datauser_fs_module.get_suite_config_file.cache_clear()
                datauser_fs_module.get_datauser_root.cache_clear()
                datauser_fs_module._load_path_overrides.cache_clear()
                try:
                    result = dataset_write_koredoc("drone_test_raw_5", "feeds2", session_id=session_id)
                finally:
                    datauser_fs_module.get_suite_root.cache_clear()
                    datauser_fs_module.get_suite_config_file.cache_clear()
                    datauser_fs_module.get_datauser_root.cache_clear()
                    datauser_fs_module._load_path_overrides.cache_clear()

            exported = data_dir / "feeds2" / "drone_test_raw_5.koredoc"
            self.assertTrue(exported.exists())
            content = exported.read_text(encoding="utf-8")

        self.assertIn("Exported dataset 'drone_test_raw_5' records 1-2 of 2", result)
        self.assertIn("KoreDocs document 'drone_test_raw_5.koredoc'", result)
        self.assertIn("at 'feeds2/drone_test_raw_5.koredoc'", result)
        self.assertNotIn("Wrote ", result)
        self.assertIn("Real story alpha", content)
        self.assertIn("Real story beta", content)
        self.assertNotIn("sample snippet", content.lower())

    def test_file_write_strips_legacy_koredocs_prefix_to_datauser_root(self) -> None:
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
                    result = file_write("KoreDocs/notes/example.koredoc", "# Hello")
                finally:
                    datauser_fs_module.get_suite_root.cache_clear()
                    datauser_fs_module.get_suite_config_file.cache_clear()
                    datauser_fs_module.get_datauser_root.cache_clear()
                    datauser_fs_module._load_path_overrides.cache_clear()

            written = data_dir / "notes" / "example.koredoc"
            self.assertTrue(written.exists())
            self.assertEqual(result, "Wrote data/notes/example.koredoc")

    def test_koredocs_korefile_migrates_legacy_db_into_filesystem_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            root_dir = tmp_root / "datauser"
            legacy_db = tmp_root / "korefile.db"

            conn = sqlite3.connect(str(legacy_db))
            try:
                conn.execute(
                    """
                    CREATE TABLE folders (
                        id INTEGER PRIMARY KEY,
                        parent_id INTEGER,
                        name TEXT NOT NULL,
                        path TEXT NOT NULL UNIQUE,
                        revision INTEGER NOT NULL DEFAULT 1,
                        modified_at TEXT,
                        created_at TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE files (
                        id INTEGER PRIMARY KEY,
                        folder_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        ext TEXT NOT NULL,
                        content BLOB,
                        metadata TEXT,
                        word_count INTEGER,
                        revision INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT,
                        modified_at TEXT
                    )
                    """
                )
                conn.execute("INSERT INTO folders (id, parent_id, name, path) VALUES (1, NULL, 'Root', '/')")
                conn.execute("INSERT INTO folders (id, parent_id, name, path) VALUES (2, 1, 'Radar', '/Radar')")
                conn.execute(
                    "INSERT INTO files (id, folder_id, name, ext, content, metadata, word_count, revision) VALUES (1, 2, 'companies.koredoc', 'koredoc', ?, '{}', 2, 1)",
                    (zlib.compress(b"# Radar Companies"),),
                )
                conn.commit()
            finally:
                conn.close()

            koredocs_korefile.configure(root_dir, legacy_db)
            koredocs_korefile.init_db()

            migrated = root_dir / "Radar" / "companies.koredoc"
            self.assertTrue(migrated.exists())
            self.assertEqual(migrated.read_text(encoding="utf-8"), "# Radar Companies")
            self.assertFalse(legacy_db.exists())
            self.assertFalse(Path(str(legacy_db) + "-wal").exists())
            self.assertFalse(Path(str(legacy_db) + "-shm").exists())

            files = koredocs_korefile.list_files(folder_path="/Radar")
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0]["name"], "companies.koredoc")
            files_alias = koredocs_korefile.list_files(folder_path="KoreDocs/Radar")
            self.assertEqual(len(files_alias), 1)
            self.assertEqual(files_alias[0]["name"], "companies.koredoc")

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

            with patch.dict(os.environ, {"KORE_SUITE_ROOT": str(tmp_root), "KORE_SUITE_DATAUSER": str(data_dir)}):
                datauser_fs_module.get_suite_root.cache_clear()
                datauser_fs_module.get_suite_config_file.cache_clear()
                datauser_fs_module.get_datauser_root.cache_clear()
                datauser_fs_module._load_path_overrides.cache_clear()
                try:
                    result = file_write(
                        "KoreDocs/feeds2/drone_test_raw_5.koredoc",
                        "### Record 1\n- **Snippet:** This is a sample snippet for entry 1.\n",
                    )
                finally:
                    datauser_fs_module.get_suite_root.cache_clear()
                    datauser_fs_module.get_suite_config_file.cache_clear()
                    datauser_fs_module.get_datauser_root.cache_clear()
                    datauser_fs_module._load_path_overrides.cache_clear()

        self.assertIn("refusing to write suspicious placeholder content", result)


if __name__ == "__main__":
    unittest.main()
