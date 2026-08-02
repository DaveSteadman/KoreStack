# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Regression tests for dynamic skill-module loading and recovery after import errors.
# ====================================================================================================

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import skill_executor


class SkillExecutorLoaderTests(unittest.TestCase):
    def test_failed_import_does_not_leave_a_partial_module_cached(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            module_path = root / "skills" / "sample.py"
            module_path.parent.mkdir()
            module_path.write_text("raise RuntimeError('broken skill')\n", encoding="utf-8")

            with patch.object(skill_executor, "get_workspace_root", return_value=root):
                with self.assertRaisesRegex(RuntimeError, "broken skill"):
                    skill_executor._load_callable_from_module_path("skills/sample.py", "sample")

                module_path.write_text("def sample():\n    return 'ready'\n", encoding="utf-8")
                loaded = skill_executor._load_callable_from_module_path("skills/sample.py", "sample")

        self.assertEqual(loaded(), "ready")


if __name__ == "__main__":
    unittest.main()
