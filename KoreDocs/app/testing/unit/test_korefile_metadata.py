from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from KoreDocs.app.documents.korefile import service as korefile


class KoreFileMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name) / "datauser"
        korefile.configure(self.root)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_metadata_preserves_nested_json_and_is_independent_of_content(self) -> None:
        metadata = {
            "profile": "analysis/v1",
            "taxonomy": {"topics": ["market-analysis", "automotive"]},
            "period": {"start": "2026-01-01", "end": "2026-03-31"},
            "flags": [True, False],
        }

        created = korefile.create_file(1, "company-analysis.koredoc", "# Original", metadata)
        updated = korefile.update_file(created["id"], "# Revised")

        self.assertEqual(created["metadata"], metadata)
        self.assertIsNotNone(updated)
        self.assertEqual(updated["metadata"], metadata)
        document = korefile.get_file(created["id"])
        self.assertEqual(document["body_content"], "# Revised")
        self.assertIn('---koredocs-json', document["content"])

        replacement = {"workflow": {"step": "published"}}
        replaced = korefile.update_file(created["id"], metadata=replacement)

        self.assertIsNotNone(replaced)
        self.assertEqual(replaced["metadata"], replacement)

    def test_metadata_survives_file_rename_and_move(self) -> None:
        metadata = {"pipeline": {"stage": "research", "run": 4}}
        created = korefile.create_file(1, "draft.koredoc", "# Draft", metadata)
        folder = korefile.create_folder("archive", 1)

        renamed = korefile.rename_file(created["id"], "final.koredoc")
        moved = korefile.move_file(renamed["id"], folder["id"])

        self.assertIsNotNone(moved)
        self.assertEqual(moved["metadata"], metadata)
        self.assertEqual(korefile.get_file(moved["id"])["metadata"], metadata)

    def test_metadata_registry_is_not_listed_as_a_document(self) -> None:
        korefile.create_file(1, "note.koredoc", "# Note", {"purpose": "test"})

        names = [file["name"] for file in korefile.list_files()]

        self.assertEqual(names, ["note.koredoc"])
