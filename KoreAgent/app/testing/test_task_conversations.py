import unittest
from unittest.mock import patch
from pathlib import Path
import sys


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import run_helpers


class TaskConversationTests(unittest.TestCase):
    def test_run_prompt_batch_seeds_history_and_saves_each_turn(self) -> None:
        captured_histories: list[list[dict]] = []
        saved_turns: list[tuple[str, str]] = []

        def fake_orchestrate_prompt(**kwargs):
            captured_histories.append(list(kwargs.get("conversation_history") or []))
            user_prompt = kwargs["user_prompt"]
            return f"reply:{user_prompt}", 123, 0, True, 4.5

        seeded_turns = [
            {"role": "user",      "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer"},
        ]

        with patch.object(run_helpers, "orchestrate_prompt", side_effect=fake_orchestrate_prompt):
            results = run_helpers.run_prompt_batch(
                ["prompt one", "prompt two"],
                session_id   = "task_alpha",
                persist_path = None,
                config       = object(),
                logger       = object(),
                seeded_turns = seeded_turns,
                save_turn_fn = lambda user_text, agent_text: saved_turns.append((user_text, agent_text)),
            )

        self.assertEqual(len(results), 2)
        self.assertEqual(
            captured_histories[0],
            [
                {"role": "user",      "content": "earlier question"},
                {"role": "assistant", "content": "earlier answer"},
            ],
        )
        self.assertEqual(
            captured_histories[1],
            [
                {"role": "user",      "content": "earlier question"},
                {"role": "assistant", "content": "earlier answer"},
                {"role": "user",      "content": "prompt one"},
                {"role": "assistant", "content": "reply:prompt one"},
            ],
        )
        self.assertEqual(
            saved_turns,
            [
                ("prompt one", "reply:prompt one"),
                ("prompt two", "reply:prompt two"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
