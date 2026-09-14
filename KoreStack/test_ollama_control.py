from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from KoreStack.ollama_control import OllamaControl


class OllamaControlTests(unittest.TestCase):
    def test_snapshot_combines_agent_configuration_and_loaded_model(self) -> None:
        control = OllamaControl("http://127.0.0.1:8765/api/status/ollama", Path(tempfile.gettempdir()))
        agent = {
            "host":         "http://127.0.0.1:11434",
            "backend":      "ollama",
            "model":        "nemotron-3.5-lightning:latest",
            "num_ctx":      32768,
            "max_predict":  4096,
            "offload_mode": "auto",
            "sampling":     {"temperature": 0.2, "top_p": 0.95},
        }
        api_state = {"models": [{"name": "nemotron-3.5-lightning:latest"}]}

        with patch("KoreStack.ollama_control._read_json", side_effect=[agent, api_state]):
            state = control.snapshot()

        self.assertTrue(state["server_running"])
        self.assertTrue(state["server_ready"])
        self.assertTrue(state["controllable"])
        self.assertEqual(state["loaded_models"], ["nemotron-3.5-lightning:latest"])
        self.assertEqual(state["configured_model"], "nemotron-3.5-lightning:latest")
        self.assertEqual(state["sampling_summary"], "temperature=0.2, top_p=0.95")

    def test_snapshot_keeps_owned_process_visible_while_server_starts(self) -> None:
        control = OllamaControl("http://127.0.0.1:8765/api/status/ollama", Path(tempfile.gettempdir()))

        class Process:
            pid = 1234
            returncode = None

            @staticmethod
            def poll() -> None:
                return None

        control._proc = Process()  # type: ignore[assignment]
        with patch("KoreStack.ollama_control._read_json", return_value=None):
            state = control.snapshot()

        self.assertTrue(state["server_running"])
        self.assertFalse(state["server_ready"])
        self.assertEqual(state["management"], "KoreStack")


if __name__ == "__main__":
    unittest.main()
