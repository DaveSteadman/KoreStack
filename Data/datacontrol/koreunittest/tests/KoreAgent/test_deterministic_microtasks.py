from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


APP_ROOT = Path(os.environ["KORESTACK_ROOT"]) / "KoreAgent" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from prompt_builder import build_system_message
from agent.orchestration.engine import _should_activate_python_for_tabular_task
from system_skills.CodeExecute.code_execute_skill import python_execute


class DeterministicMicrotaskTests(unittest.TestCase):
    def test_tabular_analysis_activates_python_but_plain_creation_does_not(self) -> None:
        self.assertTrue(
            _should_activate_python_for_tabular_task(
                "Given this CSV, identify each row with an empty field."
            )
        )
        self.assertTrue(
            _should_activate_python_for_tabular_task(
                "Read the CSV and add an AverageRevenuePerUnit column."
            )
        )
        self.assertFalse(
            _should_activate_python_for_tabular_task(
                "Generate a CSV and add five made-up rows."
            )
        )
        self.assertFalse(
            _should_activate_python_for_tabular_task(
                "Create a CSV with five rows of made-up data."
            )
        )

    def test_letter_count_is_computed_exactly(self) -> None:
        result = python_execute("word = 'accelerate'\nprint(f'e={word.count(\"e\")}')")

        self.assertEqual("e=3", result.strip())

    def test_system_prompt_requires_exact_computation(self) -> None:
        system_message = build_system_message(
            "",
            None,
            {"skills": []},
            skill_guidance_enabled=False,
            sandbox_enabled=True,
        )

        self.assertIn("use python_execute when it is available", system_message)
        self.assertIn("Do not estimate or answer from intuition", system_message)


if __name__ == "__main__":
    unittest.main()
