# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# test koreconv input module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Primary types: KoreConvInputTests.
# Function inventory:
# - test_latest_message_uses_timestamp_instead_of_response_list_position: Implements the test latest message uses timestamp instead of response list position operation for this module.
# - test_event_prompt_label_uses_the_newest_inbound_message: Implements the test event prompt label uses the newest inbound message operation for this module.
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

from input_layer.koreconv_input import _event_prompt_label
from input_layer.koreconv_input import _build_conversation_history
from input_layer.koreconv_input import _handle_compress_needed
from input_layer.koreconv_input import _latest_message
from input_layer.koreconv_input import _queue_compaction_if_needed
from input_layer.koreconv_input import _run_manual_compaction
from input_layer import koreconv_input as koreconv_input_module
from conversation_state import decode_semantic_summary
from conversation_state import encode_background_context
from context_compactor import split_messages_for_compaction
from input_layer.slash_processing import process_slash_prompt


class KoreConvInputTests(unittest.TestCase):
    def test_latest_message_uses_timestamp_instead_of_response_list_position(self) -> None:
        messages = [
            {"id": 175, "direction": "outbound", "created_at": "2026-07-18T17:25:44+00:00"},
            {"id": 179, "direction": "inbound",  "created_at": "2026-07-18T19:13:28+00:00"},
        ]

        latest = _latest_message(messages)

        self.assertIsNotNone(latest)
        self.assertEqual(latest["id"], 179)
        self.assertEqual(latest["direction"], "inbound")

    def test_event_prompt_label_uses_the_newest_inbound_message(self) -> None:
        event = {
            "conversation": {
                "messages": [
                    {"direction": "inbound",  "content": "first"},
                    {"direction": "outbound", "content": "answer"},
                    {"direction": "inbound",  "content": "latest prompt"},
                ],
            },
        }

        self.assertEqual(_event_prompt_label(event), "latest prompt")

    def test_compaction_retains_latest_three_user_turns(self) -> None:
        messages = [
            {"id": 1, "direction": "inbound",  "content": "one"},
            {"id": 2, "direction": "outbound", "content": "one reply"},
            {"id": 3, "direction": "inbound",  "content": "two"},
            {"id": 4, "direction": "outbound", "content": "two reply"},
            {"id": 5, "direction": "inbound",  "content": "three"},
            {"id": 6, "direction": "outbound", "content": "three reply"},
            {"id": 7, "direction": "inbound",  "content": "four"},
        ]

        archived, retained = split_messages_for_compaction(messages)

        self.assertEqual([message["id"] for message in archived], [1, 2])
        self.assertEqual([message["id"] for message in retained], [3, 4, 5, 6, 7])

    def test_history_omits_messages_represented_by_compacted_summary(self) -> None:
        history = _build_conversation_history(
            [
                {"id": 1, "direction": "inbound",  "content": "old request", "summarised": 1},
                {"id": 2, "direction": "outbound", "content": "old reply",   "summarised": 1},
                {"id": 3, "direction": "inbound",  "content": "recent request"},
                {"id": 4, "direction": "inbound",  "content": "current request"},
            ],
            current_inbound_id=4,
        )

        self.assertEqual(history, [{"role": "user", "content": "recent request"}])

    def test_context_threshold_queues_one_high_priority_compaction_event(self) -> None:
        posts: list[tuple[str, dict]] = []
        with patch.object(koreconv_input_module, "_http_get", return_value=[]):
            with patch.object(koreconv_input_module, "_http_post", side_effect=lambda _base, path, payload: posts.append((path, payload)) or {}):
                _queue_compaction_if_needed(
                    "http://test",
                    7,
                    token_estimate        = 80_000,
                    context_window_tokens = 100_000,
                    push_log_line         = lambda _line: None,
                )

        self.assertEqual(posts[0][0], "/events")
        self.assertEqual(posts[0][1]["event_type"], "compress_needed")
        self.assertEqual(posts[0][1]["priority"], 10)

    def test_context_threshold_does_not_duplicate_pending_compaction(self) -> None:
        posts: list[tuple[str, dict]] = []
        with patch.object(koreconv_input_module, "_http_get", return_value=[{"event_type": "compress_needed"}]):
            with patch.object(koreconv_input_module, "_http_post", side_effect=lambda _base, path, payload: posts.append((path, payload)) or {}):
                _queue_compaction_if_needed(
                    "http://test",
                    7,
                    token_estimate        = 80_000,
                    context_window_tokens = 100_000,
                    push_log_line         = lambda _line: None,
                )

        self.assertEqual(posts, [])

    def test_compact_slash_executes_its_compaction_event_immediately(self) -> None:
        handled: list[tuple[dict, object]] = []
        with patch.object(koreconv_input_module, "_http_get", return_value=[]):
            with patch.object(koreconv_input_module, "_http_post", return_value={"id": 42}):
                with patch.object(koreconv_input_module, "_handle_compress_needed", side_effect=lambda event, config, _log: handled.append((event, config))):
                    message = _run_manual_compaction(
                        "http://test",
                        {"id": 7},
                        SimpleNamespace(),
                        lambda _line: None,
        )

        self.assertIn("finished", message.lower())
        self.assertEqual(len(handled), 1)
        self.assertEqual(handled[0][0], {"id": 42, "conversation": {"id": 7}})

    def test_compact_slash_command_uses_compaction_callback(self) -> None:
        output: list[str] = []
        response = process_slash_prompt(
            "/compact",
            config           = SimpleNamespace(),
            output           = lambda text, _level="info": output.append(text),
            clear_history    = lambda: None,
            session_context  = None,
            session_id       = "test",
            compress_history = lambda: "Context compaction queued.",
        )

        self.assertEqual(response, "Context compaction queued.")
        self.assertEqual(output, ["Context compaction queued."])

    def test_compaction_persists_semantic_summary_before_marking_sources(self) -> None:
        event = {
            "id": 11,
            "conversation": {
                "id": 7,
                "background_context": encode_background_context([]),
            },
        }
        raw_messages = [
            {"id": 1, "direction": "inbound",  "content": "first request"},
            {"id": 2, "direction": "outbound", "content": "first answer"},
            {"id": 3, "direction": "inbound",  "content": "second request"},
            {"id": 4, "direction": "outbound", "content": "second answer"},
            {"id": 5, "direction": "inbound",  "content": "third request"},
            {"id": 6, "direction": "inbound",  "content": "current request"},
        ]
        patches: list[tuple[str, dict]] = []
        logs: list[str]                 = []
        completed: list[dict]           = []

        def fake_post(_base, path, payload, timeout=8):
            if path.endswith("/complete"):
                completed.append(payload)
            return {}

        result = SimpleNamespace(response="Facts:\n- The first request was answered.")
        config = SimpleNamespace(resolved_model="test-model", num_ctx=16_384)
        with patch.object(koreconv_input_module, "_get_base_url", return_value="http://test"):
            with patch.object(koreconv_input_module, "_http_get", return_value=raw_messages):
                with patch.object(koreconv_input_module, "_http_post", side_effect=fake_post):
                    with patch.object(koreconv_input_module, "_http_patch", side_effect=lambda _base, path, payload, timeout=8: patches.append((path, payload)) or {}):
                        with patch.object(koreconv_input_module, "call_llm_chat", return_value=result):
                            _handle_compress_needed(event, config, logs.append)

        conversation_patch = next(payload for path, payload in patches if path == "/conversations/7")
        summary, metadata = decode_semantic_summary(conversation_patch["background_context"])
        message_patches = [path for path, _payload in patches if path.startswith("/messages/")]
        self.assertIn("first request", summary)
        self.assertEqual(metadata["source_message_ids"], [1, 2])
        self.assertEqual(message_patches, ["/messages/1", "/messages/2"])
        self.assertTrue(any(line.startswith("[compacting] Context compacting...") for line in logs))
        self.assertEqual(completed, [{"status": "completed"}])


if __name__ == "__main__":
    unittest.main()
