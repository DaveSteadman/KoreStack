# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Test coverage for server.
# Exercises the expected behaviour and regression boundaries for this area.
# ====================================================================================================

import sys
import tempfile
import unittest
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from KoreCode.app import server


class KoreCodeServerTests(unittest.TestCase):
    @contextmanager
    def _temp_runs_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"KORECODE_RUNS_DIR": tmp}, clear=False):
                yield Path(tmp)

    @contextmanager
    def _temp_work_items_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"KORECODE_WORK_ITEMS_DIR": tmp}, clear=False):
                yield Path(tmp)

    def test_work_item_lifecycle_and_run_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self._temp_work_items_dir():
            root = Path(tmp)
            original_root       = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = root
            try:
                created = server.api_create_work_item(
                    server.WorkItemCreateBody(
                        title       = "Repair failing greeting test",
                        description = "Find and correct the regression.",
                        scope       = ["sample.py", "test_sample.py"],
                    )
                )
                updated = server.api_update_work_item(
                    created["work_item_id"],
                    server.WorkItemUpdateBody(
                        status = "investigating",
                        plan   = ["Read the failing test", "Trace the production path"],
                    ),
                )
                attached = server.attach_run(created["work_item_id"], "run-1")
                listed   = server.api_work_items()
            finally:
                server._ACTIVE_ROOT = original_root

        self.assertEqual(created["status"], "scoping")
        self.assertEqual(updated["status"], "investigating")
        self.assertEqual(updated["plan"], ["Read the failing test", "Trace the production path"])
        self.assertEqual(attached["run_ids"], ["run-1"])
        self.assertEqual(listed["work_items"][0]["work_item_id"], created["work_item_id"])

    def test_work_item_rejects_unknown_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self._temp_work_items_dir():
            original_root       = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = Path(tmp)
            try:
                created = server.api_create_work_item(server.WorkItemCreateBody(title="Check status validation"))
                with self.assertRaises(HTTPException) as context:
                    server.api_update_work_item(
                        created["work_item_id"],
                        server.WorkItemUpdateBody(status="whatever"),
                    )
            finally:
                server._ACTIVE_ROOT = original_root

        self.assertEqual(context.exception.status_code, 400)

    def test_korecode_stores_use_suite_datacontrol_root(self) -> None:
        from KoreCode.app import edit_store
        from KoreCode.app import run_store
        from KoreCode.app import work_item_store

        with tempfile.TemporaryDirectory() as tmp:
            datacontrol = Path(tmp) / "Data" / "datacontrol"
            with patch("KoreCode.app.edit_store.get_suite_datacontrol_dir", return_value=datacontrol), \
                 patch("KoreCode.app.run_store.get_suite_datacontrol_dir", return_value=datacontrol), \
                 patch("KoreCode.app.work_item_store.get_suite_datacontrol_dir", return_value=datacontrol):
                self.assertEqual(
                    edit_store._proposals_root(),
                    datacontrol / "korecode" / "edit_proposals",
                )
                self.assertEqual(
                    run_store._runs_root(),
                    datacontrol / "korecode" / "runs",
                )
                self.assertEqual(
                    work_item_store._items_root(),
                    datacontrol / "korecode" / "work_items",
                )

    def test_korechat_client_strips_ui_path_from_suite_url_map(self) -> None:
        from KoreCode.app.korechat_client import korechat_base_url

        urls = json.dumps({"korechat": "http://127.0.0.1:19602/ui"})
        with patch.dict("os.environ", {"KORE_SUITE_URLS": urls}, clear=False):
            self.assertEqual(korechat_base_url(), "http://127.0.0.1:19602")

    def test_workspace_root_is_durable(self) -> None:
        from KoreCode.app import ui_state_store

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            root      = state_dir / "workspace"
            root.mkdir()
            with patch.dict("os.environ", {"KORECODE_UI_STATE_DIR": str(state_dir)}, clear=False):
                ui_state_store.set_active_workspace_root(root)
                self.assertEqual(ui_state_store.get_active_workspace_root(), str(root.resolve()))

    def test_setting_workspace_root_persists_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root          = Path(tmp)
            original_root = server._ACTIVE_ROOT
            try:
                with patch("KoreCode.app.server.set_active_workspace_root") as persisted:
                    selected = server._set_workspace_root(str(root))
            finally:
                server._ACTIVE_ROOT = original_root

        self.assertEqual(selected, root)
        persisted.assert_called_once_with(root)

    def test_api_chat_thread_reads_without_creating_conversation(self) -> None:
        with patch("KoreCode.app.server.get_thread", return_value={"ok": True}) as mocked, \
             patch("KoreCode.app.server.find_latest_run", return_value=None):
            payload = server.api_chat_thread(path="demo.py", conversation_external_id="conv-1")
        self.assertTrue(payload["ok"])
        mocked.assert_called_once_with(
            server._workspace_root(),
            "demo.py",
            create=False,
            conversation_external_id="conv-1",
            workspace_context_enabled=True,
        )

    def test_api_chat_thread_recovers_latest_conversation_for_path(self) -> None:
        workspace_root = server._workspace_root()
        latest_run = {
            "workspace_root":           str(workspace_root),
            "path":                     "demo.py",
            "conversation_external_id": "KoreChat_recover_me",
        }
        with patch("KoreCode.app.server.find_latest_run", side_effect=[latest_run, None]), \
             patch("KoreCode.app.server.get_thread", return_value={"ok": True}) as mocked:
            payload = server.api_chat_thread(path="demo.py")

        self.assertTrue(payload["ok"])
        mocked.assert_called_once_with(
            workspace_root,
            "demo.py",
            create=False,
            conversation_external_id="KoreChat_recover_me",
            workspace_context_enabled=True,
        )

    def test_workspace_chat_recovers_latest_legacy_file_conversation(self) -> None:
        workspace_root = server._workspace_root()
        legacy_run = {
            "workspace_root":           str(workspace_root),
            "path":                     "legacy_file.py",
            "conversation_external_id": "KoreChat_project_recovery",
        }
        with patch("KoreCode.app.server.find_latest_run", side_effect=[None, legacy_run, None]) as finder, \
             patch("KoreCode.app.server.get_thread", return_value={"ok": True}) as mocked:
            payload = server.api_chat_thread(path="__workspace__")

        self.assertTrue(payload["ok"])
        self.assertEqual(finder.call_count, 3)
        mocked.assert_called_once_with(
            workspace_root,
            "__workspace__",
            create=False,
            conversation_external_id="KoreChat_project_recovery",
            workspace_context_enabled=True,
        )

    def test_chat_run_stores_routed_execution_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self._temp_runs_dir(), \
             patch("KoreCode.app.server.start_background_run"):
            original_root       = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = Path(tmp)
            try:
                payload = server.api_chat_runs(
                    server.ChatRunCreateBody(
                        user_text = "Create a new file main.py",
                        mode      = "chat",
                    )
                )
            finally:
                server._ACTIVE_ROOT = original_root

        contract = payload["run"]["context"]["execution_contract"]
        self.assertEqual(contract["id"], "create_file")
        self.assertEqual(
            contract["allowed_tools"],
            ["list_tree", "read_file", "read_context", "check_python", "run_python"],
        )

    def test_direct_tool_execution_honors_run_execution_contract(self) -> None:
        run = {
            "context": {
                "execution_contract": {
                    "allowed_tools": ["list_tree"],
                },
            },
        }
        with patch("KoreCode.app.server.get_run", return_value=run), \
             patch("KoreCode.app.server.append_tool_call"):
            payload = server.api_chat_tools_execute(
                server.ChatToolExecuteBody(
                    run_id        = "run-1",
                    tool_requests = [{"tool": "read_file", "args": {"path": "main.py"}}],
                )
            )

        self.assertFalse(payload["results"][0]["ok"])
        self.assertIn("not active", payload["results"][0]["error"])

    def test_workspace_index_rebuild_creates_markdown_and_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.py").write_text(
                "import os\n\n"
                "def greet(name):\n"
                "    return helper(name)\n\n"
                "def helper(name):\n"
                "    return name.upper()\n",
                encoding="utf-8",
            )

            original_root        = server._ACTIVE_ROOT
            server._ACTIVE_ROOT  = root
            try:
                payload = server.api_workspace_index_rebuild()
                status  = server.api_workspace_index_status()
                self.assertTrue((root / "KoreCodeWorkspace.md").exists())
                self.assertTrue((root / "KoreCodeWorkspace.sqlite3").exists())
            finally:
                server._ACTIVE_ROOT = original_root

        self.assertEqual(payload["menu_file_name"], "KoreCodeWorkspace.md")
        self.assertEqual(payload["index"]["index_file_name"], "KoreCodeWorkspace.sqlite3")
        self.assertEqual(payload["index"]["file_count"], 1)
        self.assertGreaterEqual(payload["index"]["symbol_count"], 2)
        self.assertTrue(status["menu_exists"])
        self.assertEqual(status["index"]["index_file_name"], "KoreCodeWorkspace.sqlite3")

    def test_workspace_index_symbol_and_call_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.py").write_text(
                "def helper(name):\n"
                "    return name.upper()\n\n"
                "def greet(name):\n"
                "    return helper(name)\n",
                encoding="utf-8",
            )

            original_root        = server._ACTIVE_ROOT
            server._ACTIVE_ROOT  = root
            try:
                server.api_workspace_index_rebuild()
                files   = server.api_workspace_index_files()
                symbols = server.api_workspace_index_symbols(query="greet")
                symbol  = server.api_workspace_index_symbol(qualname="greet")
                callees = server.api_workspace_index_callees(qualname="greet")
                callers = server.api_workspace_index_callers(qualname="helper")
            finally:
                server._ACTIVE_ROOT = original_root

        self.assertEqual(files["files"][0]["path"], "sample.py")
        self.assertTrue(any(item["qualname"] == "greet" for item in symbols["symbols"]))
        self.assertEqual(symbol["qualname"], "greet")
        self.assertTrue(any(item["call_qualname"] == "helper" for item in callees["callees"]))
        self.assertTrue(any(item["caller_qualname"] == "greet" for item in callers["callers"]))

    def test_api_slash_complete_returns_matching_items(self) -> None:
        payload = server.api_slash_command_complete(
            server.SlashCommandCompleteBody(
                text="/workspace r",
                current_mode="chat",
                workspace_context_enabled=True,
                thread_path="__workspace__",
                has_last_user_message=False,
                limit=5,
            )
        )
        self.assertTrue(any(item["label"] == "regen" for item in payload["items"]))

    def test_workspace_index_rebuild_allows_duplicate_symbol_names_across_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "alpha.py").write_text(
                "def helper(value):\n"
                "    return value\n",
                encoding="utf-8",
            )
            (root / "beta.py").write_text(
                "def helper(value):\n"
                "    return value.upper()\n",
                encoding="utf-8",
            )

            original_root        = server._ACTIVE_ROOT
            server._ACTIVE_ROOT  = root
            try:
                payload = server.api_workspace_index_rebuild()
                files   = server.api_workspace_index_files()
                symbols = server.api_workspace_index_symbols(query="helper")
            finally:
                server._ACTIVE_ROOT = original_root

        self.assertEqual(payload["index"]["file_count"], 2)
        self.assertEqual(len(files["files"]), 2)
        self.assertEqual(sum(1 for item in symbols["symbols"] if item["qualname"] == "helper"), 2)

    def test_api_chat_followup_passes_outbound_sender_display(self) -> None:
        with self._temp_runs_dir(), patch("KoreCode.app.server.append_internal_followup", return_value={"ok": True}) as mocked:
            payload = server.api_chat_followup(
                server.ChatFollowupBody(
                    path="demo.py",
                    prompt="continue",
                    visible_text="hidden",
                    conversation_external_id="conv-2",
                    outbound_sender_display="__korecode_internal__",
                )
            )
        self.assertTrue(payload["ok"])
        self.assertIn("run", payload)
        mocked.assert_called_once_with(
            server._workspace_root(),
            "demo.py",
            "continue",
            "hidden",
            conversation_external_id="conv-2",
            outbound_sender_display="__korecode_internal__",
            workspace_context_enabled=True,
        )

    def test_api_chat_workspace_context_passes_enabled_flag(self) -> None:
        with patch("KoreCode.app.server.set_workspace_context_enabled", return_value={"id": 12}) as mocked:
            payload = server.api_chat_workspace_context(
                server.ChatWorkspaceContextBody(
                    conversation_external_id="conv-3",
                    enabled=False,
                )
            )
        self.assertEqual(
            payload,
            {
                "ok": True,
                "enabled": False,
                "conversation_external_id": "conv-3",
                "conversation_found": True,
            },
        )
        mocked.assert_called_once_with(
            server._workspace_root(),
            "conv-3",
            False,
        )

    def test_api_chat_send_creates_persisted_run(self) -> None:
        with self._temp_runs_dir():
            thread_payload = {
                "path":             "demo.py",
                "title":            "KoreCode",
                "conversation_id":  21,
                "external_id":      "conv-21",
                "pending_response": True,
                "messages":         [],
                "raw_messages":     [],
                "last_assistant":   None,
            }
            with patch("KoreCode.app.server.append_visible_message_for_conversation", return_value=thread_payload) as mocked:
                payload = server.api_chat_send(
                    server.ChatSendBody(
                        path="demo.py",
                        visible_text="Fix the bug",
                        prompt_override="Prompt payload",
                        mode="bughunt",
                        conversation_external_id="conv-21",
                    )
                )

        self.assertEqual(payload["conversation_id"], 21)
        self.assertIn("run", payload)
        self.assertEqual(payload["run"]["run_kind"], "chat_send")
        self.assertEqual(payload["run"]["mode"], "bughunt")
        self.assertEqual(payload["run"]["status"], "waiting_agent")
        self.assertEqual(payload["run"]["input"]["text"], "Fix the bug")
        self.assertEqual(payload["run"]["conversation_external_id"], "conv-21")
        self.assertEqual(payload["run"]["conversation_id"], 21)
        self.assertTrue(any(event["event_type"] == "conversation_append_completed" for event in payload["run"]["events"]))
        mocked.assert_called_once_with(
            server._workspace_root(),
            "demo.py",
            "Fix the bug",
            "Prompt payload",
            conversation_external_id="conv-21",
            workspace_context_enabled=True,
        )

    def test_api_chat_followup_creates_persisted_run(self) -> None:
        with self._temp_runs_dir():
            thread_payload = {
                "path":             "demo.py",
                "title":            "KoreCode",
                "conversation_id":  22,
                "external_id":      "conv-22",
                "pending_response": True,
                "messages":         [],
                "raw_messages":     [],
                "last_assistant":   None,
            }
            with patch("KoreCode.app.server.append_internal_followup", return_value=thread_payload) as mocked:
                payload = server.api_chat_followup(
                    server.ChatFollowupBody(
                        path="demo.py",
                        prompt="Continue with tools",
                        visible_text="visible",
                        mode="refactor",
                        conversation_external_id="conv-22",
                        outbound_sender_display="agent",
                    )
                )

        self.assertEqual(payload["conversation_id"], 22)
        self.assertIn("run", payload)
        self.assertEqual(payload["run"]["run_kind"], "chat_followup")
        self.assertEqual(payload["run"]["mode"], "refactor")
        self.assertEqual(payload["run"]["status"], "waiting_agent")
        self.assertEqual(payload["run"]["input"]["text"], "Continue with tools")
        self.assertTrue(any(event["event_type"] == "followup_append_completed" for event in payload["run"]["events"]))
        mocked.assert_called_once_with(
            server._workspace_root(),
            "demo.py",
            "Continue with tools",
            "visible",
            conversation_external_id="conv-22",
            outbound_sender_display="agent",
            workspace_context_enabled=True,
        )

    def test_api_chat_thread_marks_waiting_run_completed_when_reply_observed(self) -> None:
        with self._temp_runs_dir():
            with patch("KoreCode.app.server.append_visible_message_for_conversation", return_value={
                "path":             "demo.py",
                "title":            "KoreCode",
                "conversation_id":  30,
                "external_id":      "conv-30",
                "pending_response": True,
                "messages":         [],
                "raw_messages":     [],
                "last_assistant":   None,
            }):
                created = server.api_chat_send(
                    server.ChatSendBody(
                        path="demo.py",
                        visible_text="Explain this",
                        prompt_override="Prompt payload",
                        conversation_external_id="conv-30",
                    )
                )

            run_id = created["run"]["run_id"]
            with patch("KoreCode.app.server.get_thread", return_value={
                "path":             "demo.py",
                "title":            "KoreCode",
                "conversation_id":  30,
                "external_id":      "conv-30",
                "pending_response": False,
                "messages":         [],
                "raw_messages":     [],
                "last_assistant":   {
                    "id":         77,
                    "created_at": "2026-06-23T12:00:00+00:00",
                    "content":    "Done.",
                },
            }):
                payload = server.api_chat_thread(path="demo.py", conversation_external_id="conv-30")

        self.assertIn("run", payload)
        self.assertEqual(payload["run"]["run_id"], run_id)
        self.assertEqual(payload["run"]["status"], "completed")
        self.assertTrue(any(event["event_type"] == "agent_reply_observed" for event in payload["run"]["events"]))

    def test_api_chat_prompt_builds_backend_prompt_with_context_and_mentions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root        = Path(tmp)
            active_file = root / "sample.py"
            mention     = root / "notes.md"
            active_file.write_text(
                "def greet(name):\n"
                "    return f'hi {name}'\n",
                encoding="utf-8",
            )
            mention.write_text(
                "# Notes\n"
                "Remember to preserve the public API.\n",
                encoding="utf-8",
            )

            original_root      = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = root
            try:
                payload = server.api_chat_prompt(
                    server.ChatPromptBuildBody(
                        mode="refactor",
                        user_text="Refactor this and also review @notes.md",
                        path="sample.py",
                        selection="return f'hi {name}'",
                        cursor={"line": 2, "column": 5, "offset": 22},
                        workspace_context_enabled=True,
                    )
                )
            finally:
                server._ACTIVE_ROOT = original_root

        prompt = payload["prompt"]
        self.assertIn("You are KoreCode Agent", prompt)
        self.assertIn("[MENTIONED_FILES]", prompt)
        self.assertIn("FILE: notes.md", prompt)
        self.assertIn("[CONTEXT_PACK]", prompt)
        self.assertIn('"path": "sample.py"', prompt)
        self.assertIn('The following code is selected in the editor:', prompt)

    def test_api_chat_tool_followup_prompt_builds_backend_prompt(self) -> None:
        payload = server.api_chat_tool_followup_prompt(
            server.ChatToolFollowupPromptBody(
                mode="bughunt",
                path="sample.py",
                user_text="Find the bug",
                previous_response='{"kind":"tool_requests"}',
                tool_results=[{"tool": "read_file", "ok": True}],
            )
        )
        prompt = payload["prompt"]
        self.assertIn("[TOOL_RESULTS]", prompt)
        self.assertIn('"tool": "read_file"', prompt)
        self.assertIn("[PREVIOUS_AGENT_RESPONSE_JSON]", prompt)
        self.assertIn("Find bugs and risks", prompt)

    def test_api_chat_tools_lists_backend_tool_contract(self) -> None:
        payload = server.api_chat_tools()
        self.assertIn("tools", payload)
        self.assertIn("read_file", payload["tools"])
        self.assertIn("replace_python_function", payload["tools"])
        self.assertEqual(payload["tools"]["read_file"]["category"], "read")
        self.assertEqual(payload["tools"]["replace_python_function"]["category"], "write")

    def test_api_chat_tools_execute_reads_file_and_logs_run_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self._temp_runs_dir():
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text(
                "def greet(name):\n"
                "    return f'hi {name}'\n",
                encoding="utf-8",
            )

            original_root       = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = root
            try:
                run = server.create_run(
                    run_kind                  = "chat_followup",
                    mode                      = "chat",
                    input_text                = "inspect",
                    visible_text              = "inspect",
                    prompt_override           = "inspect",
                    path                      = "sample.py",
                    workspace_root            = root,
                    workspace_context_enabled = True,
                    conversation_external_id  = "conv-tools",
                )
                payload = server.api_chat_tools_execute(
                    server.ChatToolExecuteBody(
                        tool_requests=[
                            {"tool": "read_file", "args": {"path": "sample.py"}},
                            {"tool": "search_in_file", "args": {"path": "sample.py", "query": "return"}},
                        ],
                        active_path="sample.py",
                        workspace_context_enabled=True,
                        run_id=run["run_id"],
                    )
                )
                updated_run = server.get_run(run["run_id"])
            finally:
                server._ACTIVE_ROOT = original_root

        self.assertEqual(len(payload["results"]), 2)
        self.assertTrue(payload["results"][0]["ok"])
        self.assertEqual(payload["results"][0]["result"]["path"], "sample.py")
        self.assertTrue(payload["results"][1]["ok"])
        self.assertEqual(updated_run["tool_calls"][0]["tool"], "read_file")
        self.assertEqual(updated_run["tool_calls"][1]["tool"], "search_in_file")

    def test_api_chat_runs_executes_backend_loop_in_python(self) -> None:
        with self._temp_runs_dir(), \
             patch("KoreCode.app.server.start_background_run", side_effect=lambda fn, *args: fn(*args)):
            with patch("KoreCode.app.server.append_visible_message_for_conversation", return_value={
                "path":             "__workspace__",
                "title":            "KoreCode",
                "conversation_id":  41,
                "external_id":      "conv-41",
                "pending_response": True,
                "messages":         [],
                "raw_messages":     [],
                "last_assistant":   None,
            }), patch("KoreCode.app.server.get_thread", side_effect=[
                {
                    "path":             "__workspace__",
                    "title":            "KoreCode",
                    "conversation_id":  41,
                    "external_id":      "conv-41",
                    "pending_response": False,
                    "messages":         [{"role": "assistant", "text": "Done."}],
                    "raw_messages":     [],
                    "last_assistant":   {
                        "id":         90,
                        "created_at": "2026-06-23T12:30:00+00:00",
                        "content":    "Done.",
                    },
                }
            ]):
                payload = server.api_chat_runs(
                    server.ChatRunCreateBody(
                        mode="chat",
                        user_text="Summarise this file",
                        thread_path="__workspace__",
                        active_path=".",
                    )
                )

        run = payload["run"]
        self.assertEqual(run["run_kind"], "chat_run")
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["conversation_external_id"], "conv-41")
        self.assertEqual(run["output"]["kind"], "assistant_text")
        self.assertEqual(run["output"]["text"], "Done.")
        self.assertTrue(any(event["event_type"] == "agent_run_completed" for event in run["events"]))
        self.assertEqual(len(run["model_responses"]), 1)

    def test_api_chat_runs_executes_tool_rounds_in_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self._temp_runs_dir():
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text(
                "def greet(name):\n"
                "    return f'hi {name}'\n",
                encoding="utf-8",
            )

            original_root       = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = root
            try:
                with patch("KoreCode.app.server.start_background_run", side_effect=lambda fn, *args: fn(*args)):
                    with patch("KoreCode.app.server.append_visible_message_for_conversation", return_value={
                        "path":             "sample.py",
                        "title":            "KoreCode",
                        "conversation_id":  51,
                        "external_id":      "conv-51",
                        "pending_response": True,
                        "messages":         [],
                        "raw_messages":     [],
                        "last_assistant":   None,
                    }), patch("KoreCode.app.server.append_internal_followup", return_value={
                        "path":             "sample.py",
                        "title":            "KoreCode",
                        "conversation_id":  51,
                        "external_id":      "conv-51",
                        "pending_response": True,
                        "messages":         [],
                        "raw_messages":     [],
                        "last_assistant":   {
                            "id":         101,
                            "created_at": "2026-06-23T12:31:00+00:00",
                            "content":    '{"kind":"tool_requests","tool_requests":[{"tool":"read_file","args":{"path":"sample.py"}}],"next":"continue"}',
                        },
                    }), patch("KoreCode.app.server.get_thread", side_effect=[
                        {
                            "path":             "sample.py",
                            "title":            "KoreCode",
                            "conversation_id":  51,
                            "external_id":      "conv-51",
                            "pending_response": False,
                            "messages":         [],
                            "raw_messages":     [],
                            "last_assistant":   {
                                "id":         101,
                                "created_at": "2026-06-23T12:31:00+00:00",
                                "content":    '{"kind":"tool_requests","tool_requests":[{"tool":"read_file","args":{"path":"sample.py"}}],"next":"continue"}',
                            },
                        },
                        {
                            "path":             "sample.py",
                            "title":            "KoreCode",
                            "conversation_id":  51,
                            "external_id":      "conv-51",
                            "pending_response": False,
                            "messages":         [{"role": "assistant", "text": "Reviewed sample.py"}],
                            "raw_messages":     [],
                            "last_assistant":   {
                                "id":         102,
                                "created_at": "2026-06-23T12:31:02+00:00",
                                "content":    "Reviewed sample.py",
                            },
                        },
                    ]):
                        payload = server.api_chat_runs(
                            server.ChatRunCreateBody(
                                mode="chat",
                                user_text="Inspect sample.py",
                                thread_path="sample.py",
                                active_path="sample.py",
                            )
                        )
            finally:
                server._ACTIVE_ROOT = original_root

        run = payload["run"]
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["output"]["text"], "Reviewed sample.py")
        self.assertEqual(len(run["tool_calls"]), 1)
        self.assertEqual(run["tool_calls"][0]["tool"], "read_file")
        self.assertTrue(run["tool_calls"][0]["ok"])
        self.assertEqual(len(run["model_responses"]), 2)

    def test_api_chat_continue_runs_executes_backend_loop_in_python(self) -> None:
        with self._temp_runs_dir(), \
             patch("KoreCode.app.server.start_background_run", side_effect=lambda fn, *args: fn(*args)):
            with patch("KoreCode.app.server.append_internal_followup", return_value={
                "path":             "__workspace__",
                "title":            "KoreCode",
                "conversation_id":  61,
                "external_id":      "conv-61",
                "pending_response": True,
                "messages":         [],
                "raw_messages":     [],
                "last_assistant":   None,
            }), patch("KoreCode.app.server.get_thread", side_effect=[
                {
                    "path":             "__workspace__",
                    "title":            "KoreCode",
                    "conversation_id":  61,
                    "external_id":      "conv-61",
                    "pending_response": False,
                    "messages":         [],
                    "raw_messages":     [],
                    "last_assistant":   {
                        "id":         120,
                        "created_at": "2026-06-23T12:40:00+00:00",
                        "content":    "\nprint('hello')\n",
                    },
                }
            ]):
                payload = server.api_chat_continue_runs(
                    server.ContinueRunCreateBody(
                        active_path="sample.py",
                        prefix="def f():\n    ",
                        suffix="return 1\n",
                        offset=12,
                    )
                )

        run = payload["run"]
        self.assertEqual(run["run_kind"], "continue_run")
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["output"]["kind"], "continue_insert")
        self.assertEqual(run["output"]["text"], "print('hello')\n")
        self.assertTrue(any(event["event_type"] == "continue_run_completed" for event in run["events"]))

    def test_api_create_and_apply_edit_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text(
                "def greet(name):\n"
                "    return f'hi {name}'\n",
                encoding="utf-8",
            )

            original_root       = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = root
            try:
                content, _encoding = server._read_text(path)
                proposal = server.api_create_edit_proposal(
                    server.EditProposalCreateBody(
                        edits=[
                            {
                                "file": "sample.py",
                                "from": 1,
                                "to":   2,
                                "replacement": "def greet(name):\n    return name.upper()\n",
                                "expected_hash": server._content_hash(content),
                                "reason": "Update greeting",
                            }
                        ],
                        source="assistant",
                        summary="Update sample.py",
                    )
                )
                applied = server.api_apply_edit_proposal(proposal["proposal_id"])
                final_content = path.read_text(encoding="utf-8")
            finally:
                server._ACTIVE_ROOT = original_root

        self.assertTrue(proposal["validation_ok"])
        self.assertEqual(proposal["status"], "proposed")
        self.assertEqual(applied["status"], "applied")
        self.assertTrue(applied["apply_result"]["ok"])
        self.assertIn("return name.upper()", final_content)

    def test_edit_proposal_can_create_and_apply_new_python_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root       = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = root
            try:
                proposal = server.api_create_edit_proposal(
                    server.EditProposalCreateBody(
                        edits=[
                            {
                                "file": "main.py",
                                "from": 1,
                                "to":   1,
                                "replacement": "print('hello world')\n",
                                "reason": "Create a minimal executable entry point.",
                            }
                        ],
                        source="assistant",
                        summary="Create main.py",
                    )
                )
                applied = server.api_apply_edit_proposal(proposal["proposal_id"])
                final_content = (root / "main.py").read_text(encoding="utf-8")
            finally:
                server._ACTIVE_ROOT = original_root

        self.assertTrue(proposal["validation_ok"])
        self.assertTrue(applied["apply_result"]["ok"])
        self.assertEqual(final_content, "print('hello world')\n")

    def test_agent_edit_application_applies_validated_workspace_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self._temp_runs_dir():
            root = Path(tmp)
            path = root / "main.py"
            path.write_text('print("Hello, KoreCode!")\n', encoding="utf-8")

            applied = server._apply_agent_edits(
                workspace_root = root,
                run_id         = "run-agent-edit",
                active_path    = "main.py",
                user_text      = "Add a file header.",
                edits          = [
                    {
                        "file":        "main.py",
                        "from":        1,
                        "to":          1,
                        "replacement": '"""Application entry point."""\n\nprint("Hello, KoreCode!")\n',
                        "explanation": "Add the requested file header.",
                    }
                ],
                summary = "Add a file header",
            )

            final_content = path.read_text(encoding="utf-8")

        self.assertTrue(applied["apply_result"]["ok"])
        self.assertEqual(applied["apply_result"]["applied"], 1)
        self.assertTrue(final_content.startswith('"""Application entry point."""'))

    def test_agent_edit_application_rejects_unrequested_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("print('main')\n", encoding="utf-8")
            (root / "other.py").write_text("print('other')\n", encoding="utf-8")

            result = server._apply_agent_edits(
                workspace_root = root,
                run_id         = "run-boundary",
                active_path    = "main.py",
                user_text      = "Add a greeting.",
                edits          = [
                    {
                        "file":        "other.py",
                        "from":        1,
                        "to":          1,
                        "replacement": "print('changed')\n",
                    }
                ],
                summary = "Unexpected edit",
            )

        self.assertFalse(result["apply_result"]["ok"])
        self.assertIn("outside the active or explicitly named files", result["apply_result"]["errors"][0])

    def test_agent_edit_application_allows_explicitly_named_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = server._apply_agent_edits(
                workspace_root = root,
                run_id         = "run-create-file",
                active_path    = "main.py",
                user_text      = "Create a new file named string_utils.py.",
                edits          = [
                    {
                        "file":        "string_utils.py",
                        "from":        1,
                        "to":          1,
                        "replacement": "def matching_lines(lines, substring):\n    return [line for line in lines if substring in line]\n",
                    }
                ],
                summary = "Create string utility",
            )

            created = (root / "string_utils.py").read_text(encoding="utf-8")

        self.assertTrue(result["apply_result"]["ok"])
        self.assertIn("matching_lines", created)

    def test_agent_create_file_playbook_allows_a_new_agent_named_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = server._apply_agent_edits(
                workspace_root      = root,
                run_id              = "run-create-file-playbook",
                active_path         = "main.py",
                user_text           = "Create a utility that filters a list of strings by a substring.",
                execution_contract  = {"id": "create_file"},
                edits               = [
                    {
                        "file":        "string_utils.py",
                        "from":        1,
                        "to":          1,
                        "replacement": "def matching_lines(lines, substring):\n    return [line for line in lines if substring in line]\n",
                    }
                ],
                summary = "Create string utility",
            )

            created = (root / "string_utils.py").read_text(encoding="utf-8")

        self.assertTrue(result["apply_result"]["ok"])
        self.assertIn("matching_lines", created)

    def test_python_runner_captures_script_output_and_syntax_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hello.py").write_text("print('hello from runner')\n", encoding="utf-8")
            (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            original_root       = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = root
            try:
                executed = server._run_python_tool("hello.py", "run", 5)
                checked  = server._run_python_tool("broken.py", "check", None)
            finally:
                server._ACTIVE_ROOT = original_root

        self.assertTrue(executed["ok"])
        self.assertIn("hello from runner", executed["stdout"])
        self.assertFalse(checked["ok"])
        self.assertIn("SyntaxError", checked["stderr"])

    def test_direct_python_execution_endpoint_runs_workspace_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hello.py").write_text("print('execution panel')\n", encoding="utf-8")
            original_root       = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = root
            try:
                result = server.api_execution_python(server.PythonExecutionBody(path="hello.py"))
            finally:
                server._ACTIVE_ROOT = original_root

        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("execution panel", result["stdout"])

    def test_write_tool_returns_edit_proposal_not_direct_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text(
                "def greet(name):\n"
                "    return f'hi {name}'\n",
                encoding="utf-8",
            )

            original_root       = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = root
            try:
                content, _encoding = server._read_text(path)
                payload = server.api_chat_tools_execute(
                    server.ChatToolExecuteBody(
                        tool_requests=[
                            {
                                "tool": "replace_python_function",
                                "args": {
                                    "path": "sample.py",
                                    "symbol": "greet",
                                    "replacement": "def greet(name):\n    return name.upper()\n",
                                    "expected_hash": server._content_hash(content),
                                },
                            }
                        ],
                        active_path="sample.py",
                        workspace_context_enabled=True,
                    )
                )
                untouched_content = path.read_text(encoding="utf-8")
            finally:
                server._ACTIVE_ROOT = original_root

        result = payload["results"][0]
        self.assertTrue(result["ok"])
        self.assertIn("proposal_id", result["result"])
        self.assertEqual(result["result"]["status"], "proposed")
        self.assertIn("return f'hi {name}'", untouched_content)

    def test_replace_python_function_requires_matching_hash_and_valid_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text(
                "def greet(name):\n"
                "    return f'hi {name}'\n",
                encoding="utf-8",
            )

            original_root = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = root
            try:
                with self.assertRaises(HTTPException) as mismatch_ctx:
                    server.api_replace_python_function(
                        server.PythonFunctionReplaceBody(
                            path="sample.py",
                            symbol="greet",
                            replacement="def greet(name):\n    return name.upper()\n",
                            expected_hash="wrong",
                        )
                    )
                self.assertEqual(mismatch_ctx.exception.status_code, 409)

                content, _encoding = server._read_text(path)
                with self.assertRaises(HTTPException) as parse_ctx:
                    server.api_replace_python_function(
                        server.PythonFunctionReplaceBody(
                            path="sample.py",
                            symbol="greet",
                            replacement="def greet(name)\n    return name.upper()\n",
                            expected_hash=server._content_hash(content),
                        )
                    )
                self.assertEqual(parse_ctx.exception.status_code, 400)

                payload = server.api_replace_python_function(
                    server.PythonFunctionReplaceBody(
                        path="sample.py",
                        symbol="greet",
                        replacement="def greet(name):\n    return name.upper()\n",
                        expected_hash=server._content_hash(content),
                    )
                )
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["symbol"], "greet")
                self.assertIn("return name.upper()", path.read_text(encoding="utf-8"))
            finally:
                server._ACTIVE_ROOT = original_root

    def test_insert_python_function_requires_matching_hash_and_valid_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text(
                "def greet(name):\n"
                "    return f'hi {name}'\n",
                encoding="utf-8",
            )

            original_root = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = root
            try:
                with self.assertRaises(HTTPException) as mismatch_ctx:
                    server.api_insert_python_function(
                        server.PythonFunctionInsertBody(
                            path="sample.py",
                            source="def bye(name):\n    return name.lower()\n",
                            expected_hash="wrong",
                        )
                    )
                self.assertEqual(mismatch_ctx.exception.status_code, 409)

                content, _encoding = server._read_text(path)
                with self.assertRaises(HTTPException) as parse_ctx:
                    server.api_insert_python_function(
                        server.PythonFunctionInsertBody(
                            path="sample.py",
                            source="def bye(name)\n    return name.lower()\n",
                            expected_hash=server._content_hash(content),
                        )
                    )
                self.assertEqual(parse_ctx.exception.status_code, 400)

                payload = server.api_insert_python_function(
                    server.PythonFunctionInsertBody(
                        path="sample.py",
                        source="def bye(name):\n    return name.lower()\n",
                        expected_hash=server._content_hash(content),
                    )
                )
                self.assertTrue(payload["ok"])
                self.assertIsNone(payload["inserted_after"])
                self.assertIsNone(payload["inserted_into"])
                self.assertIn("def bye(name):", path.read_text(encoding="utf-8"))
            finally:
                server._ACTIVE_ROOT = original_root

    def test_insert_python_function_into_class_adds_method_inside_class(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text(
                "class Greeter:\n"
                "    def hello(self):\n"
                "        return 'hi'\n",
                encoding="utf-8",
            )

            original_root = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = root
            try:
                content, _encoding = server._read_text(path)
                payload = server.api_insert_python_function(
                    server.PythonFunctionInsertBody(
                        path="sample.py",
                        source="def bye(self):\n    return 'bye'\n",
                        into_class="Greeter",
                        expected_hash=server._content_hash(content),
                    )
                )
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["inserted_into"], "Greeter")
                self.assertIn(
                    "class Greeter:\n"
                    "    def hello(self):\n"
                    "        return 'hi'\n"
                    "\n"
                    "    def bye(self):\n"
                    "        return 'bye'\n",
                    path.read_text(encoding="utf-8"),
                )
            finally:
                server._ACTIVE_ROOT = original_root


if __name__ == "__main__":
    unittest.main()
