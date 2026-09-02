# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Unit tests for config-controlled Ollama temperature and seed request options.
# ====================================================================================================

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


APP_ROOT = Path(__file__).resolve().parents[4] / "KoreAgent" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import llm_client_openai
from agent.orchestration import engine
from input_layer import slash_commands


class OllamaSamplingOptionsTests(unittest.TestCase):
    def setUp(self) -> None:
        llm_client_openai.configure_server("ollama", "http://localhost:11434")
        llm_client_openai.configure_ollama_sampling_options()

    def test_disabled_sampling_options_are_not_sent(self) -> None:
        llm_client_openai.configure_ollama_sampling_options(
            temperature         = 0.2,
            temperature_enabled = False,
            seed                = 42,
            seed_enabled        = False,
        )

        options = llm_client_openai.get_ollama_request_options()

        self.assertNotIn("temperature", options)
        self.assertNotIn("seed", options)

    def test_enabled_sampling_options_are_sent_and_round_trip(self) -> None:
        llm_client_openai.configure_ollama_sampling_options(
            temperature         = 0.2,
            temperature_enabled = True,
            seed                = 42,
            seed_enabled        = True,
        )

        options = llm_client_openai.get_ollama_request_options()

        self.assertEqual(options["temperature"], 0.2)
        self.assertEqual(options["seed"], 42)
        self.assertEqual(
            llm_client_openai.get_ollama_sampling_config(),
            {
                "temperature":         0.2,
                "temperature_enabled": True,
                "seed":                42,
                "seed_enabled":        True,
            },
        )

    def test_defaults_set_preserves_sampling_options(self) -> None:
        llm_client_openai.configure_ollama_sampling_options(0.3, True, 101, True)
        context = SimpleNamespace(
            config = SimpleNamespace(resolved_model="model", num_ctx=4096, max_predict=512),
            output = lambda *_args: None,
        )

        with tempfile.TemporaryDirectory() as directory:
            defaults_path = Path(directory) / "koreagent_config.json"
            with patch.object(slash_commands, "get_agent_config_file", return_value=defaults_path), \
                 patch.object(slash_commands, "get_active_host", return_value="http://localhost:11434"):
                slash_commands._cmd_defaults("set", context)

            saved = json.loads(defaults_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["temperature"], 0.3)
        self.assertTrue(saved["temperature_enabled"])
        self.assertEqual(saved["seed"], 101)
        self.assertTrue(saved["seed_enabled"])

    def test_orchestration_header_formats_unset_sampling_options(self) -> None:
        llm_client_openai.configure_ollama_sampling_options(0.8, True, 0, False)

        parameters = engine._format_ollama_sampling_parameters()

        self.assertEqual(parameters, "temp: 0.8 | seed: unset")
