from __future__ import annotations

import unittest

from input_layer import slash_commands


class SlashCommandRegistryTests(unittest.TestCase):
    def test_current_test_commands_are_registered_and_described(self) -> None:
        expected = {"/unittest", "/systemtest", "/systemtesttrend"}

        self.assertTrue(expected.issubset(slash_commands._REGISTRY))
        self.assertTrue(expected.issubset(slash_commands._DESCRIPTIONS))

    def test_retired_test_commands_are_not_registered(self) -> None:
        self.assertNotIn("/test", slash_commands._REGISTRY)
        self.assertNotIn("/testtrend", slash_commands._REGISTRY)
        self.assertNotIn("/test", slash_commands._DESCRIPTIONS)
        self.assertNotIn("/testtrend", slash_commands._DESCRIPTIONS)
