# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Regression tests for explicit immediate-subfolder listing in the FileAccess Skill.
# ====================================================================================================

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[4]
APP_ROOT  = REPO_ROOT / "KoreAgent" / "app"

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from system_skills.FileAccess import file_access_skill


class FolderListTests(unittest.TestCase):
    def test_folder_ls_lists_visible_immediate_folders_with_paging(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("Alpha", "bravo", "charlie", ".hidden"):
                (root / name).mkdir()
            (root / "not_a_folder.txt").write_text("file", encoding="utf-8")
            with (
                patch.object(file_access_skill, "_resolve_directory_path", return_value=root),
                patch.object(file_access_skill, "display_datauser_path", side_effect=lambda path: f"datauser/{Path(path).name}"),
            ):
                all_folders = file_access_skill.folder_ls("reports")
                first_page  = file_access_skill.folder_ls("reports", include_hidden=False, limit=2)
                second_page = file_access_skill.folder_ls("reports", include_hidden=False, offset=2, limit=2)

        self.assertIn("4 total; showing 1-4", all_folders)
        self.assertIn("datauser/.hidden/", all_folders)
        self.assertIn("3 total; showing 1-2", first_page)
        self.assertIn("datauser/Alpha/", first_page)
        self.assertIn("datauser/bravo/", first_page)
        self.assertNotIn(".hidden", first_page)
        self.assertIn("offset=2", first_page)
        self.assertIn("3 total; showing 3-3", second_page)
        self.assertIn("datauser/charlie/", second_page)


if __name__ == "__main__":
    unittest.main()
