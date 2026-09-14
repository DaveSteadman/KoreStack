# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Unit tests for the backend-neutral LLM availability probe.
# ====================================================================================================

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


APP_ROOT = Path(__file__).resolve().parents[4] / "KoreAgent" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import llm_client
import llm_client_openai


class LlmClientAvailabilityTests(unittest.TestCase):
    def tearDown(self) -> None:
        llm_client_openai.configure_server("ollama", "http://localhost:11434")

    def test_ollama_probe_uses_the_native_health_check(self) -> None:
        llm_client_openai.configure_server("ollama", "http://llm-host:11434")

        with patch.object(llm_client._ollama, "is_ollama_running", return_value=True) as probe:
            self.assertTrue(llm_client.is_llm_running())

        probe.assert_called_once_with("http://llm-host:11434")

    def test_lmstudio_requires_a_served_model(self) -> None:
        llm_client_openai.configure_server("lmstudio", "http://llm-host:1234")

        with patch.object(llm_client._lmstudio, "list_lmstudio_models", return_value=[]):
            self.assertFalse(llm_client.is_llm_running())
        with patch.object(llm_client._lmstudio, "list_lmstudio_models", return_value=["model-id"]):
            self.assertTrue(llm_client.is_llm_running())

    def test_probe_returns_false_when_the_backend_request_raises(self) -> None:
        llm_client_openai.configure_server("ollama", "http://llm-host:11434")

        with patch.object(llm_client._ollama, "is_ollama_running", side_effect=OSError("offline")):
            self.assertFalse(llm_client.is_llm_running())
