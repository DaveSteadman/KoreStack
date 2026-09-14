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
# - test_native_chat_retries_after_runner_crash: Implements the native chat retries after runner crash operation for this module.
# - test_runtime_recovery_restarts_a_stopped_local_daemon: Implements the runtime recovery restarts a stopped local daemon operation for this module.
# - test_repeated_runner_crash_falls_back_to_cpu: Implements the repeated runner crash falls back to cpu operation for this module.
# ====================================================================================================

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


APP_ROOT = Path(__file__).resolve().parents[4] / "KoreAgent" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import llm_client_ollama


class OllamaProcessWindowsTests(unittest.TestCase):
    def test_client_does_not_start_an_unavailable_server(self) -> None:
        with patch.object(llm_client_ollama._core, "get_active_host", return_value="http://localhost:11434"), \
             patch.object(llm_client_ollama, "is_ollama_running", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "KoreStack landing page"):
                llm_client_ollama.ensure_ollama_running(start_if_needed=True)

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
            models = llm_client_ollama.list_ollama_models()

        self.assertEqual(models, [])
        is_running.assert_called_once()
        ensure_running.assert_not_called()
        request_json.assert_not_called()

    def test_prompt_call_does_not_autostart_by_default(self) -> None:
        with patch.object(llm_client_ollama._core, "get_active_host", return_value="http://localhost:11434"), \
             patch.object(llm_client_ollama, "ensure_ollama_running") as ensure_running, \
             patch.object(llm_client_ollama._core, "_request_json", return_value={"response": "ok"}), \
             patch.object(llm_client_ollama._core, "log_to_session"):
            result = llm_client_ollama.call_ollama_extended(model_name="gemma4:26b", prompt="ping")

        self.assertEqual(result.response, "ok")
        ensure_running.assert_called_once_with(host="http://localhost:11434", start_if_needed=False)

    def test_native_chat_retries_after_runner_crash(self) -> None:
        runner_crash = llm_client_ollama.urllib.error.HTTPError(
            url="http://localhost:11434/api/chat",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"llama-server process has terminated: ROCm error"}'),
        )
        response = {
            "message": {"role": "assistant", "content": "Recovered."},
            "done_reason": "stop",
        }
        with patch.object(llm_client_ollama._core, "get_active_host", return_value="http://localhost:11434"), \
             patch.object(llm_client_ollama, "ensure_ollama_running"), \
             patch.object(llm_client_ollama, "_retry_after_runtime_failure", return_value=True) as recover, \
             patch.object(llm_client_ollama._core, "_request_json", side_effect=[runner_crash, response]), \
             patch.object(llm_client_ollama._core, "log_to_session"):
            result = llm_client_ollama.call_ollama_chat(
                model_name="test-model",
                messages=[{"role": "user", "content": "hi"}],
            )

        self.assertEqual(result.response, "Recovered.")
        recover.assert_called_once()

    def test_runtime_recovery_does_not_restart_a_stopped_local_daemon(self) -> None:
        with patch.object(llm_client_ollama._core, "invalidate_host_health") as invalidate, \
             patch.object(llm_client_ollama._core, "log_to_session") as log_to_session, \
             patch.object(llm_client_ollama, "is_ollama_running", return_value=False), \
             patch.object(llm_client_ollama.time, "sleep"):
            recovered = llm_client_ollama.recover_ollama_runtime(
                "http://localhost:11434",
                attempt=0,
                detail="llama-server process has terminated: ROCm error",
            )

        invalidate.assert_called_once_with("http://localhost:11434")
        self.assertFalse(recovered)
        self.assertTrue(any("KoreStack landing page" in str(call) for call in log_to_session.call_args_list))

    def test_repeated_runner_crash_falls_back_to_cpu(self) -> None:
        with patch.object(llm_client_ollama._core, "invalidate_host_health"), \
             patch.object(llm_client_ollama._core, "log_to_session"), \
             patch.object(llm_client_ollama._core, "_is_local_host", return_value=True), \
             patch.object(llm_client_ollama, "get_ollama_offload_mode", return_value="autogpu"), \
             patch.object(llm_client_ollama, "set_ollama_offload_mode") as set_offload, \
             patch.object(llm_client_ollama, "is_ollama_running", return_value=True), \
             patch.object(llm_client_ollama.time, "sleep"):
            recovered = llm_client_ollama.recover_ollama_runtime(
                "http://localhost:11434",
                attempt=1,
                detail="llama-server process has terminated: ROCm error",
            )

        set_offload.assert_called_once_with("forcecpu")
        self.assertTrue(recovered)
