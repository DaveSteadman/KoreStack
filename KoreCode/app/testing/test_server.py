# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Test coverage for server.
# Exercises the expected behaviour and regression boundaries for this area.
# ====================================================================================================

import sys
import tempfile
import unittest
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
