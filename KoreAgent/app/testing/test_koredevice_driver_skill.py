# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Test coverage for koredevice driver skill.
# Exercises the expected behaviour and regression boundaries for this area.
# ====================================================================================================

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from skills.KoreDeviceDriver import device_driver_skill as driver_skill


class KoreDeviceDriverSkillTests(unittest.TestCase):
    def test_validate_python_snippet_requires_read_driver(self) -> None:
        snippet = (
            "def read_driver(context):\n"
            "    return {'ok': True}\n"
        )
        validated = driver_skill._validate_python_snippet(snippet)
        self.assertIn("read_driver", validated)

    def test_validate_python_snippet_rejects_missing_function(self) -> None:
        with self.assertRaises(RuntimeError):
            driver_skill._validate_python_snippet("print('hello')\n")

    def test_device_driver_set_code_preserves_existing_metadata(self) -> None:
        existing = {
            "name":              "DiskDriver",
            "display_name":      "Disk Driver",
            "vendor":            "Local",
            "protocol":          "local-python",
            "transport_address": "",
            "poll_interval_sec": 5,
            "enabled":           True,
            "description":       "Checks disk space",
        }
        updated = {"name": "DiskDriver", "python_snippet": "def read_driver(context):\n    return {'ok': True}\n"}
        with patch.object(driver_skill, "device_driver_get", return_value=existing):
            with patch.object(driver_skill, "device_driver_update", return_value=updated) as update_mock:
                result = driver_skill.device_driver_set_code(
                    "DiskDriver",
                    "def read_driver(context):\n    return {'ok': True}\n",
                )
        self.assertEqual(result["name"], "DiskDriver")
        self.assertEqual(update_mock.call_args.kwargs["vendor"], "Local")
        self.assertEqual(update_mock.call_args.kwargs["enabled"], True)

    def test_device_driver_generate_from_prompt_saves_generated_code(self) -> None:
        generated = "def read_driver(context):\n    return {'ok': True, 'disk_free_gb': 10}\n"
        saved     = {"name": "DiskDriver"}
        run_result = {"ok": True, "result": {"status": "success", "disk_free_gb": 10}}
        with patch.object(driver_skill, "_generate_driver_code_with_feedback", return_value=generated):
            with patch.object(driver_skill, "_save_driver_entry", return_value=saved) as save_mock:
                with patch.object(driver_skill, "device_driver_run", return_value=run_result):
                    result = driver_skill.device_driver_generate_from_prompt(
                        name   = "DiskDriver",
                        prompt = "check free hard disk space",
                    )
        self.assertTrue(result["saved"])
        self.assertTrue(result["validated"])
        self.assertEqual(result["generated_code"], generated)
        self.assertEqual(save_mock.call_args.kwargs["python_snippet"], generated)
        self.assertEqual(result["run_result"], run_result)

    def test_device_driver_generate_from_prompt_retries_until_saved_driver_runs(self) -> None:
        broken_code = (
            "def read_driver(context):\n"
            "    return {'status': 'failed', 'error': 'boom'}\n"
        )
        fixed_code  = (
            "def read_driver(context):\n"
            "    return {'status': 'success', 'value': 42}\n"
        )
        failed_run  = {"ok": False, "error": "NameError: name 'platform' is not defined", "result": None}
        passed_run  = {"ok": True, "result": {"status": "success", "value": 42}}
        saved_entry = {"name": "DiskDriver"}

        with patch.object(driver_skill, "_generate_driver_code_with_feedback", side_effect=[broken_code, fixed_code]) as generate_mock:
            with patch.object(driver_skill, "_save_driver_entry", return_value=saved_entry) as save_mock:
                with patch.object(driver_skill, "device_driver_run", side_effect=[failed_run, passed_run]):
                    result = driver_skill.device_driver_generate_from_prompt(
                        name   = "DiskDriver",
                        prompt = "check free hard disk space",
                    )
        self.assertTrue(result["validated"])
        self.assertEqual(result["attempt_count"], 2)
        self.assertEqual(result["generated_code"], fixed_code)
        self.assertEqual(len(result["attempts"]), 2)
        self.assertEqual(result["attempts"][0]["run_result"], failed_run)
        self.assertEqual(result["attempts"][1]["run_result"], passed_run)
        self.assertEqual(generate_mock.call_args_list[1].kwargs["previous_code"], broken_code)
        self.assertEqual(generate_mock.call_args_list[1].kwargs["run_result"], failed_run)
        self.assertEqual(save_mock.call_args.kwargs["python_snippet"], fixed_code)

    def test_device_driver_generate_from_prompt_raises_after_attempt_limit(self) -> None:
        broken_code = (
            "def read_driver(context):\n"
            "    return {'status': 'failed', 'error': 'still broken'}\n"
        )
        failed_run = {"ok": False, "error": "NameError: name 'shutil' is not defined", "result": None}

        with patch.object(driver_skill, "_generate_driver_code_with_feedback", side_effect=[broken_code, broken_code]):
            with patch.object(driver_skill, "_save_driver_entry", return_value={"name": "DiskDriver"}):
                with patch.object(driver_skill, "device_driver_run", side_effect=[failed_run, failed_run]):
                    with self.assertRaises(RuntimeError) as exc:
                        driver_skill.device_driver_generate_from_prompt(
                            name         = "DiskDriver",
                            prompt       = "check free hard disk space",
                            max_attempts = 2,
                        )
        self.assertIn("did not validate", str(exc.exception))

    def test_generate_driver_code_prompt_requires_explicit_imports(self) -> None:
        captured = {}

        class _FakeResult:
            response = "import shutil\n\ndef read_driver(context):\n    return {'ok': True}\n"

        def fake_call_llm_chat(*, model_name, messages, tools, num_ctx):
            captured["messages"] = messages
            return _FakeResult()

        with patch.object(driver_skill, "_get_active_model", return_value="test-model"):
            with patch.object(driver_skill, "_get_active_num_ctx", return_value=8192):
                with patch.object(driver_skill, "_call_llm_chat", side_effect=fake_call_llm_chat):
                    snippet = driver_skill._generate_driver_code("DiskDriver", "check free hard disk space")

        self.assertIn("import shutil", snippet)
        self.assertIn("Every module or symbol used by the snippet must be imported explicitly", captured["messages"][0]["content"])

    def test_run_result_is_success_rejects_failed_status(self) -> None:
        self.assertFalse(driver_skill._run_result_is_success({"ok": True, "result": {"status": "failed"}}))
        self.assertFalse(driver_skill._run_result_is_success({"ok": True, "result": {"error": "boom"}}))
        self.assertTrue(driver_skill._run_result_is_success({"ok": True, "result": {"status": "success"}}))

    def test_request_json_uses_device_driver_service_url(self) -> None:
        suite_cfg = {
            "network":  {"host": "127.0.0.1"},
            "services": {"koredevicedriver": {"port": 9615}},
        }

        class _DummyResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"ok": True}).encode("utf-8")

        with patch.object(driver_skill, "_read_suite_config", return_value=suite_cfg):
            with patch("urllib.request.urlopen", return_value=_DummyResponse()) as open_mock:
                result = driver_skill._request_json(method="GET", path="/status")

        self.assertEqual(result, {"ok": True})
        request = open_mock.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:9615/status")


if __name__ == "__main__":
    unittest.main()
