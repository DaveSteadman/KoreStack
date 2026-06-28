# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Test coverage for slash commands.
# Exercises the expected behaviour and regression boundaries for this area.
# ====================================================================================================

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from KoreCode.app.slash_command_context import KoreCodeSlashCommandContext
from KoreCode.app import slash_commands


class KoreCodeSlashCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        slash_commands.initialize(workspace_root_getter=lambda: Path.cwd())

    def test_help_lists_workspace_regen(self) -> None:
        lines = []
        ctx   = KoreCodeSlashCommandContext(
            output                    = lambda text, level="info": lines.append((text, level)),
            current_mode              = "chat",
            workspace_context_enabled = False,
            thread_path               = "__workspace__",
        )
        handled = slash_commands.handle("/help", ctx)
        self.assertTrue(handled)
        self.assertTrue(any("/workspace" in text and "regen" in text for text, _ in lines))

    def test_workspace_on_sets_state_and_rebuilds(self) -> None:
        lines = []
        ctx   = KoreCodeSlashCommandContext(
            output                    = lambda text, level="info": lines.append((text, level)),
            current_mode              = "chat",
            workspace_context_enabled = False,
            thread_path               = "__workspace__",
        )
        with patch("KoreCode.app.slash_command_handlers_workspace.rebuild_workspace_artifacts", return_value={"menu_file_name": "KoreCodeWorkspace.md", "file_count": 12, "index": {"index_file_name": "KoreCodeWorkspace.sqlite3"}}):
            handled = slash_commands.handle("/workspace on", ctx)
        self.assertTrue(handled)
        self.assertEqual(ctx.actions, [{"type": "set_workspace_context", "enabled": True}])
        self.assertTrue(any("Workspace context enabled." in text for text, _ in lines))

    def test_workspace_regen_emits_no_state_action(self) -> None:
        ctx = KoreCodeSlashCommandContext(
            output                    = lambda text, level="info": None,
            current_mode              = "chat",
            workspace_context_enabled = True,
            thread_path               = "__workspace__",
        )
        with patch("KoreCode.app.slash_command_handlers_workspace.rebuild_workspace_artifacts", return_value={"menu_file_name": "KoreCodeWorkspace.md", "file_count": 7, "index": {"index_file_name": "KoreCodeWorkspace.sqlite3"}}):
            handled = slash_commands.handle("/workspace regen", ctx)
        self.assertTrue(handled)
        self.assertEqual(ctx.actions, [])

    def test_retry_without_last_message_returns_error(self) -> None:
        lines = []
        ctx   = KoreCodeSlashCommandContext(
            output                    = lambda text, level="info": lines.append((text, level)),
            current_mode              = "chat",
            workspace_context_enabled = True,
            thread_path               = "file.py",
            has_last_user_message     = False,
        )
        handled = slash_commands.handle("/retry", ctx)
        self.assertTrue(handled)
        self.assertEqual(ctx.actions, [])
        self.assertTrue(any("No previous user prompt" in text for text, _ in lines))

    def test_mode_continue_requests_continue_action(self) -> None:
        ctx = KoreCodeSlashCommandContext(
            output                    = lambda text, level="info": None,
            current_mode              = "chat",
            workspace_context_enabled = True,
            thread_path               = "file.py",
        )
        handled = slash_commands.handle("/continue", ctx)
        self.assertTrue(handled)
        self.assertEqual(
            ctx.actions,
            [{"type": "set_mode", "mode": "continue", "run_continue": True}],
        )

    def test_complete_lists_matching_commands(self) -> None:
        ctx = KoreCodeSlashCommandContext(
            output                    = lambda text, level="info": None,
            current_mode              = "chat",
            workspace_context_enabled = True,
            thread_path               = "__workspace__",
        )
        items = slash_commands.complete("/wo", ctx)
        self.assertTrue(any(item["label"] == "/workspace" for item in items))

    def test_complete_lists_workspace_subcommands(self) -> None:
        ctx = KoreCodeSlashCommandContext(
            output                    = lambda text, level="info": None,
            current_mode              = "chat",
            workspace_context_enabled = True,
            thread_path               = "__workspace__",
        )
        items = slash_commands.complete("/workspace r", ctx)
        self.assertEqual(items[0]["label"], "regen")
        self.assertEqual(items[0]["value"], "/workspace regen ")


if __name__ == "__main__":
    unittest.main()
