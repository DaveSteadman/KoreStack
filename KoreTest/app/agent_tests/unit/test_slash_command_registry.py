# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# test slash command registry module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Primary types: SlashCommandRegistryTests.
# Function inventory:
# - test_retired_commands_are_not_registered: Implements the test retired commands are not registered operation for this module.
# - test_chat_is_canonical_and_session_remains_a_compatibility_alias: Implements the test chat is canonical and session remains a compatibility alias operation for this module.
# ====================================================================================================

from __future__ import annotations

import unittest

from input_layer import slash_commands


class SlashCommandRegistryTests(unittest.TestCase):
    def test_retired_commands_are_not_registered(self) -> None:
        for command in {"/test", "/testtrend", "/newchat", "/task", "/tasks", "/ctx", "/kccompress", "/cronprompt", "/cronprompts", "/unittest", "/systemtest", "/systemtesttrend", "/planning", "/workflow", "/workflows"}:
            self.assertNotIn(command, slash_commands._REGISTRY)
            self.assertNotIn(command, slash_commands._DESCRIPTIONS)

    def test_chat_is_canonical_and_session_remains_a_compatibility_alias(self) -> None:
        self.assertIn("/chat", slash_commands._REGISTRY)
        self.assertIn("/chat", slash_commands._DESCRIPTIONS)
        self.assertIn("/session", slash_commands._REGISTRY)
        self.assertNotIn("/session", slash_commands._DESCRIPTIONS)
