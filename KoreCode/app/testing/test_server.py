import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from KoreCode.app import server


class KoreCodeServerTests(unittest.TestCase):
    def test_api_chat_thread_reads_without_creating_conversation(self) -> None:
        with patch("KoreCode.app.server.get_thread", return_value={"ok": True}) as mocked:
            payload = server.api_chat_thread(path="demo.py", conversation_external_id="conv-1")
        self.assertEqual(payload, {"ok": True})
        mocked.assert_called_once_with(
            server._workspace_root(),
            "demo.py",
            create=False,
            conversation_external_id="conv-1",
            workspace_context_enabled=True,
        )

    def test_api_chat_followup_passes_outbound_sender_display(self) -> None:
        with patch("KoreCode.app.server.append_internal_followup", return_value={"ok": True}) as mocked:
            payload = server.api_chat_followup(
                server.ChatFollowupBody(
                    path="demo.py",
                    prompt="continue",
                    visible_text="hidden",
                    conversation_external_id="conv-2",
                    outbound_sender_display="__korecode_internal__",
                )
            )
        self.assertEqual(payload, {"ok": True})
        mocked.assert_called_once_with(
            server._workspace_root(),
            "demo.py",
            "continue",
            "hidden",
            conversation_external_id="conv-2",
            outbound_sender_display="__korecode_internal__",
            workspace_context_enabled=True,
        )

    def test_api_chat_workspace_context_passes_enabled_flag(self) -> None:
        with patch("KoreCode.app.server.set_workspace_context_enabled", return_value={"id": 12}) as mocked:
            payload = server.api_chat_workspace_context(
                server.ChatWorkspaceContextBody(
                    conversation_external_id="conv-3",
                    enabled=False,
                )
            )
        self.assertEqual(
            payload,
            {
                "ok": True,
                "enabled": False,
                "conversation_external_id": "conv-3",
                "conversation_found": True,
            },
        )
        mocked.assert_called_once_with(
            server._workspace_root(),
            "conv-3",
            False,
        )

    def test_replace_python_function_requires_matching_hash_and_valid_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text(
                "def greet(name):\n"
                "    return f'hi {name}'\n",
                encoding="utf-8",
            )

            original_root = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = root
            try:
                with self.assertRaises(HTTPException) as mismatch_ctx:
                    server.api_replace_python_function(
                        server.PythonFunctionReplaceBody(
                            path="sample.py",
                            symbol="greet",
                            replacement="def greet(name):\n    return name.upper()\n",
                            expected_hash="wrong",
                        )
                    )
                self.assertEqual(mismatch_ctx.exception.status_code, 409)

                content, _encoding = server._read_text(path)
                with self.assertRaises(HTTPException) as parse_ctx:
                    server.api_replace_python_function(
                        server.PythonFunctionReplaceBody(
                            path="sample.py",
                            symbol="greet",
                            replacement="def greet(name)\n    return name.upper()\n",
                            expected_hash=server._content_hash(content),
                        )
                    )
                self.assertEqual(parse_ctx.exception.status_code, 400)

                payload = server.api_replace_python_function(
                    server.PythonFunctionReplaceBody(
                        path="sample.py",
                        symbol="greet",
                        replacement="def greet(name):\n    return name.upper()\n",
                        expected_hash=server._content_hash(content),
                    )
                )
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["symbol"], "greet")
                self.assertIn("return name.upper()", path.read_text(encoding="utf-8"))
            finally:
                server._ACTIVE_ROOT = original_root

    def test_insert_python_function_requires_matching_hash_and_valid_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text(
                "def greet(name):\n"
                "    return f'hi {name}'\n",
                encoding="utf-8",
            )

            original_root = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = root
            try:
                with self.assertRaises(HTTPException) as mismatch_ctx:
                    server.api_insert_python_function(
                        server.PythonFunctionInsertBody(
                            path="sample.py",
                            source="def bye(name):\n    return name.lower()\n",
                            expected_hash="wrong",
                        )
                    )
                self.assertEqual(mismatch_ctx.exception.status_code, 409)

                content, _encoding = server._read_text(path)
                with self.assertRaises(HTTPException) as parse_ctx:
                    server.api_insert_python_function(
                        server.PythonFunctionInsertBody(
                            path="sample.py",
                            source="def bye(name)\n    return name.lower()\n",
                            expected_hash=server._content_hash(content),
                        )
                    )
                self.assertEqual(parse_ctx.exception.status_code, 400)

                payload = server.api_insert_python_function(
                    server.PythonFunctionInsertBody(
                        path="sample.py",
                        source="def bye(name):\n    return name.lower()\n",
                        expected_hash=server._content_hash(content),
                    )
                )
                self.assertTrue(payload["ok"])
                self.assertIsNone(payload["inserted_after"])
                self.assertIsNone(payload["inserted_into"])
                self.assertIn("def bye(name):", path.read_text(encoding="utf-8"))
            finally:
                server._ACTIVE_ROOT = original_root

    def test_insert_python_function_into_class_adds_method_inside_class(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text(
                "class Greeter:\n"
                "    def hello(self):\n"
                "        return 'hi'\n",
                encoding="utf-8",
            )

            original_root = server._ACTIVE_ROOT
            server._ACTIVE_ROOT = root
            try:
                content, _encoding = server._read_text(path)
                payload = server.api_insert_python_function(
                    server.PythonFunctionInsertBody(
                        path="sample.py",
                        source="def bye(self):\n    return 'bye'\n",
                        into_class="Greeter",
                        expected_hash=server._content_hash(content),
                    )
                )
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["inserted_into"], "Greeter")
                self.assertIn(
                    "class Greeter:\n"
                    "    def hello(self):\n"
                    "        return 'hi'\n"
                    "\n"
                    "    def bye(self):\n"
                    "        return 'bye'\n",
                    path.read_text(encoding="utf-8"),
                )
            finally:
                server._ACTIVE_ROOT = original_root


if __name__ == "__main__":
    unittest.main()
