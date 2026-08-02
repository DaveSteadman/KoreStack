from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from KoreDocs.app.documents.korefile import service as korefile
from KoreDocs.app.mcp import tools_koredoc


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

    def test_metadata_inventory_reports_nested_paths_and_values(self) -> None:
        korefile.create_file(1, "one.koredoc", "# One", {"artefact_type": "brief", "producer": {"service": "KoreAgent"}})
        korefile.create_file(1, "two.koredoc", "# Two", {"artifact_type": "brief", "producer": {"service": "KoreDocs"}})

        inventory = tools_koredoc.koredocs_metadata_inventory()
        fields = {field["path"]: field for field in inventory["fields"]}

        self.assertEqual(inventory["document_count"], 2)
        self.assertIn("artefact_type", fields)
        self.assertIn("artifact_type", fields)
        self.assertEqual(fields["producer.service"]["document_count"], 2)
        variants = tools_koredoc.koredocs_metadata_find_variants("artefact_type")
        self.assertEqual(variants["variant_count"], 2)

    def test_metadata_migrations_dry_run_then_update_header_only(self) -> None:
        created = korefile.create_file(1, "draft.koredoc", "# Original body", {"artifact_type": "brief", "status": "in progress"})

        preview = tools_koredoc.koredocs_metadata_rename_field("artifact_type", "artefact_type")
        self.assertFalse(preview["applied"])
        self.assertEqual(korefile.get_file(created["id"])["metadata"]["artifact_type"], "brief")

        applied = tools_koredoc.koredocs_metadata_rename_field("artifact_type", "artefact_type", apply_changes=True)
        value_change = tools_koredoc.koredocs_metadata_replace_value("status", "in progress", "in_progress", apply_changes=True)
        document = korefile.get_file(created["id"])

        self.assertTrue(applied["applied"])
        self.assertTrue(value_change["applied"])
        self.assertEqual(document["body_content"], "# Original body")
        self.assertEqual(document["metadata"], {"artefact_type": "brief", "status": "in_progress"})
