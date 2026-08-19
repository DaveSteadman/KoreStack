# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# test datauser fs module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Primary types: DataUserFilesystemTests.
# Function inventory:
# - test_resolve_rejects_absolute_and_traversal_paths: Implements the test resolve rejects absolute and traversal paths operation for this module.
# - test_dot_relative_path_resolves_inside_datauser: Implements the test dot relative path resolves inside datauser operation for this module.
# - test_write_and_delete_reject_stale_etags: Implements the test write and delete reject stale etags operation for this module.
# - test_listing_stays_in_selected_root_and_applies_filters: Implements the test listing stays in selected root and applies filters operation for this module.
# ====================================================================================================

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from KoreCommon.datauser_fs import DataUserConflictError
from KoreCommon.datauser_fs import DataUserPathError
from KoreCommon.datauser_fs import delete_file
from KoreCommon.datauser_fs import file_etag
from KoreCommon.datauser_fs import list_datauser_files
from KoreCommon.datauser_fs import resolve_datauser_path
from KoreCommon.datauser_fs import write_text_file


class DataUserFilesystemTests(unittest.TestCase):
    def test_resolve_rejects_absolute_and_traversal_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "datauser"

            for path in ("../secret.txt", "folder/../../secret.txt", Path(temporary_directory) / "secret.txt"):
                with self.assertRaises(DataUserPathError):
                    resolve_datauser_path(path, root_dir=root)

    def test_dot_relative_path_resolves_inside_datauser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "datauser"

            target = resolve_datauser_path("./CountryNews/2026-07/Malta.md", root_dir=root)

            self.assertEqual(target, root / "CountryNews" / "2026-07" / "Malta.md")

    def test_write_and_delete_reject_stale_etags(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "datauser"
            target = write_text_file("notes/status.txt", "first", root_dir=root)
            original_etag = file_etag(target, root_dir=root)

            write_text_file("notes/status.txt", "second", root_dir=root)

            with self.assertRaises(DataUserConflictError):
                write_text_file("notes/status.txt", "third", expected_etag=original_etag, root_dir=root)
            with self.assertRaises(DataUserConflictError):
                delete_file("notes/status.txt", expected_etag=original_etag, root_dir=root)

            self.assertEqual(target.read_text(encoding="utf-8"), "second")

    def test_listing_stays_in_selected_root_and_applies_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "datauser"
            write_text_file("reports/alpha-report.txt", "alpha", root_dir=root)
            write_text_file("reports/beta-report.md", "beta", root_dir=root)
            write_text_file("reports/nested/alpha-notes.txt", "nested", root_dir=root)

            files = list_datauser_files(
                search_root        = "reports",
                keywords           = ["alpha"],
                allowed_extensions = {".txt"},
                root_dir           = root,
            )

            self.assertEqual(
                [path.relative_to(root).as_posix() for path in files],
                ["reports/alpha-report.txt", "reports/nested/alpha-notes.txt"],
            )
            with self.assertRaises(DataUserPathError):
                list_datauser_files(search_root="../outside", root_dir=root)
