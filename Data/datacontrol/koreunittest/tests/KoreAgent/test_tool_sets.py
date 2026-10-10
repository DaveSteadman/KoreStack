# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# test tool sets module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Primary types: ToolSetTests.
# Function inventory:
# - test_active_tool_queue_evicts_oldest_and_reactivation_moves_to_newest: Active tool queue evicts the oldest tool and reactivation moves a tool to the newest slot.
# ====================================================================================================

from __future__ import annotations

import sys
import unittest
import os
from pathlib import Path


REPO_ROOT = Path(os.environ["KORESTACK_ROOT"])
APP_ROOT  = REPO_ROOT / "KoreAgent" / "app"

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from sessions import tool_state


class ToolSetTests(unittest.TestCase):
    def test_active_tool_queue_evicts_oldest_and_reactivation_moves_to_newest(self) -> None:
        session_id = "tool_set_fifo_test"
        current = [f"tool_{index}" for index in range(tool_state.MAX_ACTIVE_TOOLS)]
        conversation = {"tools_active": list(current)}
        tool_state.clear_session_tools_active(session_id)

        added = tool_state.promote_selected_tools(
            ["tool_new"],
            session_id=session_id,
            conversation_entry=conversation,
            persist=False,
        )
        reactivated = tool_state.promote_selected_tools(
            ["tool_4"],
            session_id=session_id,
            conversation_entry=conversation,
            persist=False,
        )

        self.assertEqual(added["evicted"], ["tool_0"])
        self.assertEqual(added["active_tools"][-1], "tool_new")
        self.assertEqual(reactivated["active_tools"][-1], "tool_4")
        self.assertEqual(len(reactivated["active_tools"]), tool_state.MAX_ACTIVE_TOOLS)
