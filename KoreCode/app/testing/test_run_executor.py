"""Regression tests for the server-side agent execution loop."""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from KoreCode.app.run_executor import AgentRunServices
from KoreCode.app.run_executor import ChatRunRequest
from KoreCode.app.run_executor import execute_chat_run


class RunExecutorTests(TestCase):
    def test_analysis_envelope_with_tool_requests_continues_the_agent_loop(self) -> None:
        replies = iter(
            [
                {
                    "external_id": "conversation-1",
                    "last_assistant": {
                        "id":      "first",
                        "content": '{"kind":"analysis","tool_requests":[{"tool":"read_file","args":{"path":"main.py"}}],"next":"continue"}',
                    },
                },
                {
                    "external_id": "conversation-1",
                    "last_assistant": {
                        "id":      "second",
                        "content": '{"kind":"final","summary":"Created the requested file.","next":"done"}',
                    },
                },
            ]
        )
        tool_requests = []
        run_updates   = []
        followups     = []

        services = AgentRunServices(
            append_visible_message_for_conversation = lambda *_args, **_kwargs: {"external_id": "conversation-1"},
            append_internal_followup                = lambda *_args, **_kwargs: {"external_id": "conversation-1"},
            get_thread                              = lambda *_args, **_kwargs: {},
            build_tool_followup_prompt              = lambda **kwargs: followups.append(kwargs) or "Use the tool result and continue.",
            execute_tool_requests                   = lambda **kwargs: tool_requests.append(kwargs["tool_requests"]) or [{
                "request_index": 0,
                "tool":          "read_file",
                "ok":            True,
                "result":        {"path": "main.py", "content": "print('hello')\n"},
            }],
            append_tool_call                        = lambda *_args, **_kwargs: None,
            append_model_response                   = lambda *_args, **_kwargs: None,
            apply_agent_edits                       = lambda **_kwargs: {},
            set_run_output                          = lambda *_args, **_kwargs: None,
            update_run                              = lambda *_args, **kwargs: run_updates.append(kwargs),
        )
        request = ChatRunRequest(
            run_id                    = "run-1",
            workspace_root            = Path("."),
            thread_path               = "__workspace__",
            active_path               = "main.py",
            selection                 = None,
            cursor                    = None,
            user_text                 = "Create a string utility file.",
            prompt                    = "Create a string utility file.",
            mode                      = "chat",
            workspace_context_enabled = True,
        )

        with patch("KoreCode.app.run_executor._wait_for_agent_turn", side_effect=lambda **_kwargs: next(replies)):
            execute_chat_run(request, services)

        self.assertEqual(tool_requests, [[{"tool": "read_file", "args": {"path": "main.py"}}]])
        self.assertTrue(followups[0]["force_completion"])
        self.assertEqual(followups[0]["tool_results"][0]["result"]["path"], "main.py")
        self.assertIn("completed", [update.get("status") for update in run_updates])
