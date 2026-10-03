# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Focused tests for the independent Ollama System One decision client and its slash configuration.
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

import llm_client_system_one
from input_layer import slash_command_handlers_models
from input_layer.slash_command_context import SlashCommandContext


class SystemOneClientTests(unittest.TestCase):
    def test_call_posts_typed_request_and_normalises_usage(self) -> None:
        response = {
            "model": "clef:27b",
            "answers": {"harm": {"type": "noul", "noul": 0.996}},
            "usage": {"input_tokens": 12, "output_tokens": 1},
        }
        with patch.object(llm_client_system_one._core, "get_active_host", return_value="http://localhost:11434"), \
             patch.object(llm_client_system_one._core, "get_active_system_one_model", return_value="clef:27b"), \
             patch.object(llm_client_system_one._core, "get_llm_timeout", return_value=60), \
             patch.object(llm_client_system_one._core, "log_to_session"), \
             patch.object(llm_client_system_one._core, "_request_json", return_value=response) as request_json:
            result = llm_client_system_one.call_system_one(
                state="send_email(to=all-customers)",
                questions={"harm": {"type": "noul", "instructions": "Could this cause harm?"}},
            )

        self.assertEqual(result.answers["harm"]["noul"], 0.996)
        self.assertEqual(result.usage, {"input_tokens": 12, "output_tokens": 1})
        self.assertEqual(request_json.call_args.kwargs["url"], "http://localhost:11434/v1/systemone")
        self.assertEqual(request_json.call_args.kwargs["payload"]["keep_alive"], -1)

    def test_rejects_a_question_without_instructions(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires instructions"):
            llm_client_system_one.call_system_one(
                state="test",
                questions={"route": {"type": "choice", "criteria": {"a": None, "b": None}}},
            )

    def test_system_one_slash_command_selects_and_warms_model(self) -> None:
        output: list[tuple[str, str]] = []
        config = SimpleNamespace(
            resolved_model   = "chat:latest",
            system_one_model = "clef:27b",
            num_ctx          = 8192,
            max_predict      = 1024,
        )
        context = SlashCommandContext(
            config        = config,
            output        = lambda text, level: output.append((text, level)),
            clear_history = lambda: None,
        )
        with patch.object(slash_command_handlers_models, "list_ollama_models", return_value=["chat:latest", "clef:27b"]), \
             patch.object(slash_command_handlers_models, "register_system_one_model") as register_model, \
             patch.object(slash_command_handlers_models, "preload_system_one_model") as preload_model:
            slash_command_handlers_models._cmd_systemone("model clef:27b", context)

        self.assertEqual(config.system_one_model, "clef:27b")
        register_model.assert_called_once_with("clef:27b")
        preload_model.assert_called_once_with("clef:27b")
        self.assertEqual(output[-1][1], "success")


if __name__ == "__main__":
    unittest.main()
