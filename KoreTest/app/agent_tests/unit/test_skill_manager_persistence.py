# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Tests durable Skill registry writes when the data directory is briefly locked.
# ====================================================================================================

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


APP_ROOT = Path(__file__).resolve().parents[4] / "KoreAgent" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import skill_manager
import skill_executor


def _skill() -> dict[str, object]:
    return {
        "name":                  "sample",
        "purpose":               "A test Skill.",
        "selection_description": "Used by persistence tests.",
        "tools": [{
            "name":       "sample_tool",
            "purpose":    "A test tool.",
            "parameters": {"type": "object", "properties": {}},
            "module":     "sample_module",
            "function":   "sample_tool",
        }],
    }


class SkillManagerPersistenceTests(unittest.TestCase):
    def test_save_retries_a_transient_windows_file_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "skill_registry.json"
            manager       = skill_manager.SkillManager(registry_path)
            original      = skill_manager.os.replace
            attempts      = 0

            def replace(source: object, target: object) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError("locked")
                original(source, target)

            with patch.object(skill_manager.os, "replace", side_effect=replace), \
                 patch.object(skill_manager.time, "sleep") as sleep:
                manager.register_skill(_skill(), service_id="test", transport="builtin")

            self.assertEqual(attempts, 2)
            sleep.assert_called_once_with(0.05)
            saved = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["skills"][0]["name"], "sample")

    def test_identical_registration_does_not_rewrite_a_clean_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = skill_manager.SkillManager(Path(directory) / "skill_registry.json")
            manager.register_skill(_skill(), service_id="test", transport="builtin")

            with patch.object(manager, "_save") as save:
                manager.register_skill(_skill(), service_id="test", transport="builtin")

            save.assert_not_called()

    def test_directory_not_found_is_a_tool_error(self) -> None:
        self.assertTrue(skill_executor.is_skill_error("Directory not found: datauser/qwqw"))
