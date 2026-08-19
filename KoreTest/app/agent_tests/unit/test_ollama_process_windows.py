# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# test ollama process windows module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Primary types: OllamaProcessWindowsTests.
# Function inventory:
# - test_server_start_hides_its_console_window: Implements the test server start hides its console window operation for this module.
# - test_status_probe_prefers_http_api: Implements the test status probe prefers http api operation for this module.
# - test_passive_model_listing_does_not_autostart: Implements the test passive model listing does not autostart operation for this module.
# - test_prompt_call_does_not_autostart_by_default: Implements the test prompt call does not autostart by default operation for this module.
# ====================================================================================================

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


APP_ROOT = Path(__file__).resolve().parents[4] / "KoreAgent" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import llm_client_ollama


@unittest.skipUnless(hasattr(subprocess, "CREATE_NO_WINDOW"), "Windows process flags are unavailable")
class OllamaProcessWindowsTests(unittest.TestCase):
    def test_server_start_hides_its_console_window(self) -> None:
        with patch.object(llm_client_ollama.subprocess, "Popen") as popen:
            llm_client_ollama.start_ollama_server()

        flags = popen.call_args.kwargs["creationflags"]
        self.assertNotEqual(flags & subprocess.CREATE_NO_WINDOW, 0)
        self.assertNotEqual(flags & subprocess.DETACHED_PROCESS, 0)
        self.assertNotEqual(flags & subprocess.CREATE_NEW_PROCESS_GROUP, 0)

    def test_status_probe_prefers_http_api(self) -> None:
        payload = {"models": [{"name": "gemma4:26b", "size": 0, "size_vram": 0, "digest": "abc", "details": {}}]}
        with patch.object(llm_client_ollama._core, "get_active_host", return_value="http://localhost:11434"), \
             patch.object(llm_client_ollama._core, "_request_json", return_value=payload) as request_json:
            rows = llm_client_ollama.get_ollama_ps_rows()

        self.assertEqual([row["name"] for row in rows], ["gemma4:26b"])
        request_json.assert_called_once()

    def test_passive_model_listing_does_not_autostart(self) -> None:
        with patch.object(llm_client_ollama, "is_ollama_running", return_value=False) as is_running, \
             patch.object(llm_client_ollama, "ensure_ollama_running") as ensure_running, \
             patch.object(llm_client_ollama._core, "_request_json") as request_json:
            models = llm_client_ollama.list_ollama_models(start_if_needed=False)

        self.assertEqual(models, [])
        is_running.assert_called_once()
        ensure_running.assert_not_called()
        request_json.assert_not_called()

    def test_prompt_call_does_not_autostart_by_default(self) -> None:
        with patch.object(llm_client_ollama._core, "get_active_host", return_value="http://localhost:11434"), \
             patch.object(llm_client_ollama._core, "get_local_ollama_autostart_enabled", return_value=False), \
             patch.object(llm_client_ollama, "ensure_ollama_running") as ensure_running, \
             patch.object(llm_client_ollama._core, "_request_json", return_value={"response": "ok"}), \
             patch.object(llm_client_ollama._core, "log_to_session"):
            result = llm_client_ollama.call_ollama_extended(model_name="gemma4:26b", prompt="ping")

        self.assertEqual(result.response, "ok")
        ensure_running.assert_called_once_with(host="http://localhost:11434", start_if_needed=False)
