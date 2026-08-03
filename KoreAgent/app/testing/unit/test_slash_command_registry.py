from __future__ import annotations

import unittest

from input_layer import slash_commands


class SlashCommandRegistryTests(unittest.TestCase):
    def test_current_test_commands_are_registered_and_described(self) -> None:
        expected = {"/unittest", "/systemtest", "/systemtesttrend"}

        self.assertTrue(expected.issubset(slash_commands._REGISTRY))
        self.assertTrue(expected.issubset(slash_commands._DESCRIPTIONS))

    def test_retired_commands_are_not_registered(self) -> None:
        for command in {"/test", "/testtrend", "/newchat", "/task", "/tasks"}:
            self.assertNotIn(command, slash_commands._REGISTRY)
            self.assertNotIn(command, slash_commands._DESCRIPTIONS)

    def test_chat_is_canonical_and_session_remains_a_compatibility_alias(self) -> None:
        self.assertIn("/chat", slash_commands._REGISTRY)
        self.assertIn("/chat", slash_commands._DESCRIPTIONS)
        self.assertIn("/session", slash_commands._REGISTRY)
        self.assertNotIn("/session", slash_commands._DESCRIPTIONS)

    def test_workflow_commands_are_registered_with_workflows_as_listing_command(self) -> None:
        self.assertIn("/workflow", slash_commands._REGISTRY)
        self.assertIn("/workflow", slash_commands._DESCRIPTIONS)
        self.assertIn("/workflows", slash_commands._REGISTRY)
        self.assertIn("/workflows", slash_commands._DESCRIPTIONS)

    def test_cronprompt_commands_are_canonical(self) -> None:
        self.assertIn("/cronprompt", slash_commands._REGISTRY)
        self.assertIn("/cronprompt", slash_commands._DESCRIPTIONS)
        self.assertIn("/cronprompts", slash_commands._REGISTRY)
        self.assertIn("/cronprompts", slash_commands._DESCRIPTIONS)
