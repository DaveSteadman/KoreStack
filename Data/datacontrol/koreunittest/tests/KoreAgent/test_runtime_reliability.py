# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Regression coverage for service-wide runtime policy, LLM configuration snapshots, and the shared
# execution queue's timeout and persistence guarantees.
# ====================================================================================================

from __future__ import annotations

import os
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


APP_ROOT = Path(os.environ["KORESTACK_ROOT"]) / "KoreAgent" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import execution_queue
import llm_client
import llm_client_core
from agent.orchestration import runtime_flags


class RuntimePolicyTests(unittest.TestCase):
    def tearDown(self) -> None:
        runtime_flags.set_sandbox_enabled(True)
        runtime_flags.set_web_skills_enabled(True)
        runtime_flags.set_skill_guidance_enabled(False)

    def test_policy_snapshot_is_immutable_and_service_scoped(self) -> None:
        runtime_flags.set_sandbox_enabled(False)
        runtime_flags.set_web_skills_enabled(False)

        snapshot = runtime_flags.get_runtime_policy()
        runtime_flags.set_sandbox_enabled(True)

        self.assertFalse(snapshot.sandbox_enabled)
        self.assertFalse(snapshot.web_skills_enabled)
        self.assertTrue(runtime_flags.get_sandbox_enabled())

    def test_web_skill_filter_does_not_reuse_stale_payload_identity_cache(self) -> None:
        payload = {
            "skills": [
                {"skill_name": "Web Search", "functions": ["web_search(query: str)"]},
                {"skill_name": "Files", "functions": ["folder_ls(path: str)"]},
            ],
        }

        first = runtime_flags.filter_web_skills(payload)
        payload["skills"].append({"skill_name": "Data", "functions": ["data_get()"]})
        second = runtime_flags.filter_web_skills(payload)

        self.assertEqual([skill["skill_name"] for skill in first["skills"]], ["Files"])
        self.assertEqual([skill["skill_name"] for skill in second["skills"]], ["Files", "Data"])


class LlmRuntimeConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        llm_client_core.configure_host("http://localhost:11434")
        llm_client_core.register_session_config("test-model", 131072, 1024)
        llm_client_core.set_llm_timeout(600)

    def test_host_validation_rejects_non_host_url_components(self) -> None:
        for host in (
            "ftp://localhost:11434",
            "http://user:password@localhost:11434",
            "http://localhost:11434/api",
            "http://localhost:bad-port",
        ):
            with self.assertRaises(ValueError, msg=host):
                llm_client_core.configure_host(host)

    def test_runtime_snapshot_isolated_from_later_reconfiguration(self) -> None:
        llm_client_core.configure_host("http://first-host:11434")
        llm_client_core.register_session_config("first-model", 8192, 256)
        llm_client_core.set_llm_timeout(42)

        snapshot = llm_client_core.get_llm_runtime_config()
        llm_client_core.configure_host("http://second-host:11434")
        llm_client_core.register_session_config("second-model", 16384, 512)

        self.assertEqual(snapshot.host, "http://first-host:11434")
        self.assertEqual(snapshot.model, "first-model")
        self.assertEqual(snapshot.num_ctx, 8192)
        self.assertEqual(snapshot.timeout, 42)

    def test_chat_uses_one_runtime_snapshot_for_default_request_options(self) -> None:
        llm_client_core.configure_host("http://snapshot-host:11434")
        llm_client_core.register_session_config("snapshot-model", 4096, 256)
        llm_client_core.set_llm_timeout(33)

        with patch.object(llm_client._ollama, "call_ollama_chat", return_value=object()) as call:
            llm_client.call_llm_chat("requested-model", [{"role": "user", "content": "hello"}])

        self.assertEqual(call.call_args.kwargs["host"], "http://snapshot-host:11434")
        self.assertEqual(call.call_args.kwargs["num_ctx"], 4096)
        self.assertEqual(call.call_args.kwargs["timeout"], 33)


class TaskQueueReliabilityTests(unittest.TestCase):
    def test_timeout_releases_later_work_and_persists_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            control_dir = Path(directory)
            slow_started = threading.Event()
            release_slow = threading.Event()
            fast_finished = threading.Event()

            def slow_task() -> None:
                slow_started.set()
                release_slow.wait(5)

            with patch.object(execution_queue, "_TIMEOUT_POLL_SECS", 0.01):
                state_path = control_dir / "koreagent" / "task_queue.json"
                queue = execution_queue.TaskQueue(state_path=state_path)
                try:
                    self.assertTrue(queue.enqueue("slow", "api_chat", slow_task, timeout_seconds=0.02))
                    self.assertTrue(queue.enqueue("fast", "api_chat", fast_finished.set))
                    self.assertTrue(slow_started.wait(1))
                    self.assertTrue(fast_finished.wait(1))
                    deadline = time.monotonic() + 1
                    while queue.get_state()["queue_count"] and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertEqual(queue.get_state()["queue_count"], 0)

                    with state_path.open(encoding="utf-8") as state_file:
                        state = json.load(state_file)
                    self.assertIn("last_terminal", state)
                    self.assertEqual(state["last_terminal"]["state"], "timed_out")
                    self.assertEqual(queue.get_state()["last_terminal"]["state"], "timed_out")
                finally:
                    release_slow.set()
                    queue.stop()
                    queue._worker.join()
