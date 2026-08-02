from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch


CODE_DIR = Path(__file__).resolve().parents[2]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from system_skills.WorkerChats import worker_chat_runtime as runtime
from scratchpad import scratchpad_clear
from scratchpad import scratchpad_load
from sessions.runtime import bind_session


class WorkerChatRuntimeTests(unittest.TestCase):
    def test_record_write_retries_a_transient_file_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            record = {"chat_id": "chat_retry", "status": "queued"}
            original_replace = Path.replace
            calls = 0

            def lock_once(source: Path, target: Path):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise PermissionError("temporary Dropbox lock")
                return original_replace(source, target)

            with patch.object(runtime, "get_controldata_dir", return_value=root):
                with patch.object(runtime.time, "sleep") as sleep:
                    with patch.object(Path, "replace", autospec=True, side_effect=lock_once):
                        runtime._write(record)
                    restored = runtime._read("chat_retry")

            self.assertEqual(calls, 2)
            sleep.assert_called_once_with(0.05)
            self.assertEqual(restored, record)

    def test_json_result_contract_rejects_invalid_json_before_persistence(self) -> None:
        result, error = runtime._persist_result(
            {"result_target": "", "result_format": "JSON object"},
            "not JSON",
        )

        self.assertEqual(result["summary"], "not JSON")
        self.assertIn("not valid JSON", error)

    def test_default_result_target_saves_to_parent_prompt_result(self) -> None:
        with bind_session("worker_parent"):
            scratchpad_clear()
            result, error = runtime._persist_result(
                {"parent_session_id": "worker_parent"},
                "worker answer",
            )

            self.assertEqual(error, "")
            self.assertEqual(result["saved_keys"], ["prompt_result"])
            self.assertEqual(scratchpad_load("prompt_result"), "worker answer")

    def test_chat_result_reports_terminal_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            record = {
                "chat_id": "chat_complete",
                "session_id": "worker_chat_chat_complete",
                "status": "completed",
                "result": {"summary": "done", "error": ""},
            }
            with patch.object(runtime, "get_controldata_dir", return_value=root):
                runtime._write(record)
                result = runtime.chat_result("chat_complete")

        self.assertTrue(result["ready"])
        self.assertEqual(result["result"]["summary"], "done")

    def test_cancelled_worker_is_terminal_and_is_not_run_later(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            record = {"chat_id": "chat_cancel", "session_id": "worker_chat_chat_cancel", "status": "queued", "result": {}}
            with patch.object(runtime, "get_controldata_dir", return_value=root):
                runtime._write(record)
                cancelled = runtime.chat_cancel("chat_cancel")
                runtime._run("chat_cancel")
                stored = runtime._read("chat_cancel")

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(stored["status"], "cancelled")
