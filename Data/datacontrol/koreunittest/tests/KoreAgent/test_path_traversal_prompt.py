from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


APP_ROOT = Path(os.environ["KORESTACK_ROOT"]) / "KoreAgent" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from prompt_builder import build_system_message


class PathTraversalPromptTests(unittest.TestCase):
    def test_system_prompt_requires_explicit_traversal_refusal(self) -> None:
        system_message = build_system_message(
            "",
            None,
            {"skills": []},
            skill_guidance_enabled=False,
            sandbox_enabled=True,
        )

        self.assertIn("Preserve a user-supplied filesystem path exactly.", system_message)
        self.assertIn(
            "Never remove path segments, substitute a safe path, or claim a write succeeded",
            system_message,
        )

    def test_system_prompt_requires_reading_fetched_full_text(self) -> None:
        system_message = build_system_message(
            "",
            None,
            {"skills": []},
            skill_guidance_enabled=False,
            sandbox_enabled=True,
        )

        self.assertIn("then call working_data_get on the fetched collection", system_message)
        self.assertIn("Fetching persists text outside the prompt; it does not read that text.", system_message)


if __name__ == "__main__":
    unittest.main()
