# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Tests explicit scheduled-email publication and post-tool diagnostic formatting.
# ====================================================================================================

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


APP_ROOT = Path(__file__).resolve().parents[4] / "KoreAgent" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from agent.tool_runtime import loop as tool_loop
from prompt_builder import build_system_message
from prompt_builder import get_explicit_delivery_publication_chat_name
from sessions import tool_selection
from tool_result import ToolCallResult


class _Logger:
    def log(self, _message: str = "") -> None:
        pass

    def log_file_only(self, _message: str = "") -> None:
        pass

    def log_section(self, _title: str) -> None:
        pass

    def log_section_file_only(self, _title: str) -> None:
        pass


class _Result:
    def __init__(self, response: str, tool_calls: list[dict] | None = None) -> None:
        self.response          = response
        self.message           = {"content": response}
        self.prompt_tokens     = 10
        self.completion_tokens = 5
        self.tokens_per_second = 1.0
        self.tool_calls        = tool_calls or []


class DeliveryPublicationTests(unittest.TestCase):
    def test_scheduled_email_prompt_requires_explicit_publication(self) -> None:
        conversation = {
            "channel_type": "classic_email",
            "external_id":  "webchat_cron_daily_ai_news",
        }

        prompt = build_system_message(
            "",
            None,
            {"skills": []},
            skill_guidance_enabled=False,
            sandbox_enabled=True,
            conversation_entry=conversation,
        )

        self.assertEqual(
            "webchat_cron_daily_ai_news",
            get_explicit_delivery_publication_chat_name(conversation),
        )
        self.assertIn("Scheduled email publication is mandatory", prompt)
        self.assertIn("delivery_publish_html", prompt)

    def test_non_scheduled_email_does_not_require_publication(self) -> None:
        conversation = {"channel_type": "classic_email", "external_id": "kccomms:123"}

        self.assertEqual("", get_explicit_delivery_publication_chat_name(conversation))

    def test_tool_loop_requires_confirmed_html_publication_before_completion(self) -> None:
        publication_call = {
            "id":   "publish_1",
            "type": "function",
            "function": {
                "name": "delivery_publish_html",
                "arguments": '{"chat_name":"webchat_cron_daily_ai_news","html_body":"<html><body><p>Report</p></body></html>"}',
            },
        }
        responses = [
            _Result("The dataset is ready."),
            _Result("", [publication_call]),
            _Result("Published the report."),
        ]
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "prepare daily news"},
        ]
        context_map = [
            {"round": 0, "role": "sys", "label": "system", "chars": 6, "auto_key": None, "msg_idx": 0},
            {"round": 0, "role": "user", "label": "prompt", "chars": 18, "auto_key": None, "msg_idx": 1},
        ]

        def call_llm_chat(**_kwargs):
            return responses.pop(0)

        def execute_tool_call(name, arguments, *_args):
            return ToolCallResult(
                tool      = name,
                function  = name,
                module    = "KoreComms",
                arguments = arguments,
                result    = {"published": True, "message_id": "smtp:123"},
            )

        with patch.object(tool_loop, "execute_tool_call", side_effect=execute_tool_call), \
             patch.object(tool_selection, "note_tool_used"):
            response, _prompt, _completion, successful, _tps, outputs = tool_loop.run_tool_loop(
                config                       = SimpleNamespace(resolved_model="test", max_iterations=4, num_ctx=8192, skills_payload={"skills": []}),
                messages                     = messages,
                tool_defs                    = [],
                catalog_gates                = {},
                active_tool_names            = set(),
                context_map                  = context_map,
                user_prompt                  = "prepare daily news",
                logger                       = _Logger(),
                quiet                        = True,
                call_llm_chat                = call_llm_chat,
                stop_requested               = lambda: False,
                clear_stop                   = lambda: None,
                required_publication_chat_name = "webchat_cron_daily_ai_news",
            )

        self.assertTrue(successful)
        self.assertEqual("Published the report.", response)
        self.assertEqual(["delivery_publish_html"], [output.tool for output in outputs])
        self.assertIn("Publication is still required", "\n".join(str(message.get("content") or "") for message in messages))

    def test_tool_output_formatter_handles_a_result(self) -> None:
        rendered = tool_loop.format_tool_outputs([
            ToolCallResult(
                tool      = "delivery_publish_html",
                function  = "delivery_publish_html",
                module    = "KoreComms",
                arguments = {},
                result    = {"published": True},
            ),
        ])

        self.assertIn("delivery_publish_html", rendered)
