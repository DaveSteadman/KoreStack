from __future__ import annotations

import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(os.environ["KORESTACK_ROOT"])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from KoreDocs.app.documents.korefile import service as korefile
from KoreDocs.app.mcp import tools_common, tools_koredoc, tools_koresheet


class KoreDocsMcpRevisionGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name) / "datauser"
        korefile.configure(self.root)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_existing_file_mutation_tools_require_expected_revision(self) -> None:
        mutation_tools = (
            tools_common.update_file,
            tools_common.delete_file,
            tools_common.koredocs_file_update,
            tools_common.koredocs_file_delete,
            tools_koredoc.koredocs_doc_section_replace,
            tools_koredoc.koredocs_doc_section_insert,
            tools_koredoc.koredocs_doc_markdown_append,
            tools_koresheet.koredocs_sheet_rows_update,
            tools_koresheet.koredocs_sheet_headers_set,
            tools_koresheet.koredocs_sheet_table_rows_append,
            tools_koresheet.koredocs_sheet_named_value_set,
            tools_koresheet.koredocs_sheet_cells_write,
            tools_koresheet.koredocs_sheet_rows_append,
            tools_koresheet.koredocs_sheet_rows_upsert,
            tools_koresheet.koredocs_sheet_range_clear,
        )

        for tool in mutation_tools:
            with self.subTest(tool=tool.__name__):
                revision_parameter = inspect.signature(tool).parameters["expected_revision"]
                self.assertIs(revision_parameter.default, inspect.Parameter.empty)

    def test_missing_revision_is_rejected_before_file_access(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected_revision is required"):
            tools_common.update_file(id=1, content="# Replacement", expected_revision=None)

    def test_stale_revision_returns_current_revision_without_overwriting(self) -> None:
        created = korefile.create_file(1, "revision-guard.koredoc", "# Original")
        stale_revision = created["revision"]
        korefile.update_file(created["id"], "# Newer content")

        with self.assertRaises(korefile.ConflictError) as raised:
            tools_common.update_file(
                id=created["id"],
                content="# Must not replace newer content",
                expected_revision=stale_revision,
            )

        self.assertIn("current revision:", str(raised.exception))
        self.assertIn("reread the file", str(raised.exception))
        self.assertEqual(korefile.get_file(created["id"])["body_content"], "# Newer content")


if __name__ == "__main__":
    unittest.main()
