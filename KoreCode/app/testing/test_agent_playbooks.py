from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from KoreCode.app.agent_playbooks import route_task
from KoreCode.app.prompt_builder import build_prompt_by_mode
from KoreCode.app.tool_api import execute_tool_requests


class AgentPlaybookTests(unittest.TestCase):
    def test_router_selects_create_file_playbook(self) -> None:
        playbook = route_task(user_text="Create a new file main.py", mode="chat")
        self.assertEqual(playbook.identifier, "create_file")
        self.assertTrue(playbook.permits_edits)
        self.assertNotIn("replace_python_function", playbook.allowed_tools)

        concise_playbook = route_task(user_text="Create main.py", mode="chat")
        self.assertEqual(concise_playbook.identifier, "create_file")

    def test_router_selects_diagnose_failing_test_playbook(self) -> None:
        playbook = route_task(user_text="Fix this failing test", mode="chat")
        self.assertEqual(playbook.identifier, "diagnose_failing_test")
        self.assertIn("get_python_function", playbook.allowed_tools)
        self.assertNotIn("replace_python_function", playbook.allowed_tools)

    def test_router_selects_python_debug_playbook(self) -> None:
        playbook = route_task(user_text="Run and debug main.py", mode="chat")
        self.assertEqual(playbook.identifier, "run_and_debug_python")
        self.assertIn("run_python", playbook.allowed_tools)
        self.assertIn("check_python", playbook.allowed_tools)

        concise_playbook = route_task(user_text="Run main.py", mode="chat")
        self.assertEqual(concise_playbook.identifier, "run_and_debug_python")

        bare_playbook = route_task(user_text="run", mode="chat")
        self.assertEqual(bare_playbook.identifier, "run_and_debug_python")

        active_file_playbook = route_task(user_text="Run the file", mode="chat")
        self.assertEqual(active_file_playbook.identifier, "run_and_debug_python")

    def test_explore_prompt_only_advertises_active_tools(self) -> None:
        contract = route_task(user_text="Explain this subsystem", mode="explain").payload()
        prompt = build_prompt_by_mode(
            mode                      = "explain",
            user_text                 = "Explain this subsystem",
            path                      = ".",
            selection                 = None,
            cursor                    = None,
            workspace_context_enabled = False,
            workspace_root            = REPO_ROOT,
            resolve_relative_path     = lambda value: REPO_ROOT / value,
            is_probably_text          = lambda _path: True,
            read_text                 = lambda _path: ("", "utf-8"),
            build_context_pack        = lambda *_args, **_kwargs: None,
            execution_contract        = contract,
        )
        self.assertIn("Active playbook: Explore workspace", prompt)
        self.assertIn('"read_file"', prompt)
        self.assertNotIn("replace_python_function", prompt)
        self.assertIn("Do not emit edits for this investigation-only task.", prompt)
        self.assertIn("capability_request", prompt)
        self.assertIn("not applied changes", prompt)
        self.assertIn("Do not claim file contents", prompt)

    def test_create_file_prompt_requires_target_existence_check(self) -> None:
        contract = route_task(user_text="Create hello_world.py", mode="chat").payload()
        prompt = build_prompt_by_mode(
            mode                      = "chat",
            user_text                 = "Create hello_world.py",
            path                      = ".",
            selection                 = None,
            cursor                    = None,
            workspace_context_enabled = False,
            workspace_root            = REPO_ROOT,
            resolve_relative_path     = lambda value: REPO_ROOT / value,
            is_probably_text          = lambda _path: True,
            read_text                 = lambda _path: ("", "utf-8"),
            build_context_pack        = lambda *_args, **_kwargs: None,
            execution_contract        = contract,
        )
        self.assertIn("verify the target does not exist", prompt)

    def test_edit_prompt_requires_read_before_edit_and_reports_auto_apply(self) -> None:
        contract = route_task(user_text="Add a file header", mode="chat").payload()
        prompt = build_prompt_by_mode(
            mode                      = "chat",
            user_text                 = "Add a file header",
            path                      = "main.py",
            selection                 = None,
            cursor                    = None,
            workspace_context_enabled = False,
            workspace_root            = Path.cwd(),
            resolve_relative_path     = lambda path: Path(path),
            is_probably_text          = lambda _path: True,
            read_text                 = lambda _path: ("print('hello')\n", "utf-8"),
            build_context_pack        = lambda *_args, **_kwargs: {},
            execution_contract        = contract,
        )
        self.assertIn("request read_file for that file before emitting edits", prompt)
        self.assertIn("apply validated edits automatically", prompt)
        self.assertIn("final edits response is the only autonomous write path", prompt)

    def test_executor_rejects_inactive_tool(self) -> None:
        results = execute_tool_requests(
            tool_requests                    = [{"tool": "read_file", "args": {"path": "main.py"}}],
            active_path                      = None,
            workspace_context_enabled        = False,
            read_file_fn                     = lambda _path: {"content": ""},
            read_context_fn                  = lambda *_args: {},
            list_tree_fn                     = lambda _path: {},
            get_python_function_fn           = lambda *_args: {},
            run_python_fn                    = lambda *_args: {},
            replace_python_function_proposal_fn = lambda *_args: {},
            insert_python_function_proposal_fn  = lambda *_args: {},
            allowed_tools                    = ("list_tree",),
        )
        self.assertFalse(results[0]["ok"])
        self.assertIn("not active", results[0]["error"])

    def test_executor_runs_active_python_tool(self) -> None:
        results = execute_tool_requests(
            tool_requests                    = [{"tool": "run_python", "args": {"path": "demo.py", "timeout_seconds": 7}}],
            active_path                      = "demo.py",
            workspace_context_enabled        = False,
            read_file_fn                     = lambda _path: {"content": ""},
            read_context_fn                  = lambda *_args: {},
            list_tree_fn                     = lambda _path: {},
            get_python_function_fn           = lambda *_args: {},
            run_python_fn                    = lambda path, mode, timeout: {"path": path, "mode": mode, "timeout": timeout},
            replace_python_function_proposal_fn = lambda *_args: {},
            insert_python_function_proposal_fn  = lambda *_args: {},
            allowed_tools                    = ("run_python",),
        )
        self.assertTrue(results[0]["ok"])
        self.assertEqual(results[0]["result"]["mode"], "run")
        self.assertEqual(results[0]["result"]["timeout"], 7)

    def test_executor_accepts_legacy_python_function_replacement_argument_names(self) -> None:
        calls = []
        results = execute_tool_requests(
            tool_requests                    = [{
                "tool": "replace_python_function",
                "args": {
                    "path":          "demo.py",
                    "function_name": "greet",
                    "content_hash":  "known-hash",
                    "replacement":  "def greet():\n    return 'hello'\n",
                },
            }],
            active_path                      = "demo.py",
            workspace_context_enabled        = False,
            read_file_fn                     = lambda _path: {"content": ""},
            read_context_fn                  = lambda *_args: {},
            list_tree_fn                     = lambda _path: {},
            get_python_function_fn           = lambda *_args: {},
            run_python_fn                    = lambda *_args: {},
            replace_python_function_proposal_fn = lambda *args: calls.append(args) or {},
            insert_python_function_proposal_fn  = lambda *_args: {},
            allowed_tools                    = ("replace_python_function",),
        )

        self.assertTrue(results[0]["ok"])
        self.assertEqual(calls, [("demo.py", "greet", "def greet():\n    return 'hello'\n", "known-hash")])

    def test_executor_rejects_running_a_non_active_python_file(self) -> None:
        results = execute_tool_requests(
            tool_requests                    = [{"tool": "run_python", "args": {"path": "other.py"}}],
            active_path                      = "demo.py",
            workspace_context_enabled        = False,
            read_file_fn                     = lambda _path: {"content": ""},
            read_context_fn                  = lambda *_args: {},
            list_tree_fn                     = lambda _path: {},
            get_python_function_fn           = lambda *_args: {},
            run_python_fn                    = lambda *_args: {},
            replace_python_function_proposal_fn = lambda *_args: {},
            insert_python_function_proposal_fn  = lambda *_args: {},
            allowed_tools                    = ("run_python",),
        )
        self.assertFalse(results[0]["ok"])
        self.assertIn("active Python file", results[0]["error"])


if __name__ == "__main__":
    unittest.main()
