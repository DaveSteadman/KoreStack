from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


REPO_ROOT = Path(os.environ["KORESTACK_ROOT"])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from KoreDocs.app.documents.korefile import service as korefile
from KoreDocs.app.api.routes_korefile import register_korefile_routes


class KoreFileCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name) / 'datauser'
        korefile.configure(self.root)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_copy_file_preserves_content_and_metadata_and_uses_a_new_name(self) -> None:
        source = korefile.create_file(1, 'draft.koredoc', '# Draft', {'status': 'review'})

        copied = korefile.copy_file(source['id'], 1, expected_revision=source['revision'])

        self.assertEqual(copied['name'], 'draft (copy).koredoc')
        self.assertEqual(copied['metadata'], {'status': 'review'})
        self.assertNotEqual(copied['artifact_id'], source['artifact_id'])
        self.assertEqual(korefile.get_file(copied['id'])['body_content'], '# Draft')

    def test_copy_folder_recursively_copies_files_and_empty_folders(self) -> None:
        source = korefile.create_folder('Projects', 1)
        nested = korefile.create_folder('Nested', source['id'])
        empty = korefile.create_folder('Empty', source['id'])
        source_file = korefile.create_file(source['id'], 'overview.koredoc', '# Overview', {'team': 'docs'})
        nested_file = korefile.create_file(nested['id'], 'sheet.koresheet', (
            '{"version":1,"meta":{"title":"Sheet"},"cols":2,"rows":2,"cells":{}}'
        ))
        nested_content = '{"version":1,"meta":{"title":"Sheet"},"cols":2,"rows":2,"cells":{}}'

        current_source = korefile.get_folder_by_path('/Projects')
        copied = korefile.copy_folder(source['id'], 1, expected_revision=current_source['revision'])
        copied_nested = korefile.get_folder_by_path('/Projects (copy)/Nested')
        copied_empty = korefile.get_folder_by_path('/Projects (copy)/Empty')

        self.assertEqual(copied['name'], 'Projects (copy)')
        self.assertIsNotNone(copied_nested)
        self.assertIsNotNone(copied_empty)
        copied_source_file = korefile.list_files(folder_id=copied['id'])[0]
        self.assertEqual(copied_source_file['name'], source_file['name'])
        self.assertEqual(copied_source_file['metadata'], source_file['metadata'])
        copied_nested_file = korefile.list_files(folder_id=copied_nested['id'])[0]
        self.assertEqual(copied_nested_file['name'], nested_file['name'])
        self.assertEqual(
            korefile.get_file(copied_nested_file['id'])['content'],
            nested_content,
        )

    def test_copy_folder_rejects_a_destination_inside_the_source(self) -> None:
        source = korefile.create_folder('Projects', 1)
        nested = korefile.create_folder('Nested', source['id'])

        with self.assertRaisesRegex(ValueError, 'descendants'):
            korefile.copy_folder(source['id'], nested['id'])

    def test_copy_file_rejects_a_stale_revision(self) -> None:
        source = korefile.create_file(1, 'draft.txt', 'draft')
        korefile.update_file(source['id'], 'changed')

        with self.assertRaises(korefile.ConflictError):
            korefile.copy_file(source['id'], 1, expected_revision=source['revision'])

    def test_internal_koredocs_folders_are_not_listed_for_user_operations(self) -> None:
        (self.root / '.koredocs_history' / 'internal').mkdir(parents=True)
        (self.root / '.koredocs_proposals' / 'internal').mkdir(parents=True)

        paths = {folder['path'] for folder in korefile.list_folders()}

        self.assertNotIn('/.koredocs_history', paths)
        self.assertNotIn('/.koredocs_history/internal', paths)
        self.assertNotIn('/.koredocs_proposals', paths)
        self.assertNotIn('/.koredocs_proposals/internal', paths)

    def test_copy_api_routes_report_copied_records(self) -> None:
        app = FastAPI()
        register_korefile_routes(app, data_dir=self.root)
        client = TestClient(app)
        source_folder = korefile.create_folder('Source', 1)
        source_file = korefile.create_file(source_folder['id'], 'note.txt', 'note')
        source_folder = korefile.get_folder_by_path('/Source')

        copied_file_response = client.post('/api/files/copy', json={
            'items': [{'id': source_file['id'], 'expected_revision': str(source_file['revision'])}],
            'destination_folder_id': 1,
        })
        copied_folder_response = client.post('/api/folders/copy', json={
            'items': [{'id': source_folder['id'], 'expected_revision': str(source_folder['revision'])}],
            'destination_folder_id': 1,
        })

        self.assertEqual(copied_file_response.status_code, 200)
        self.assertEqual(copied_file_response.json()['copied'][0]['name'], 'note.txt')
        self.assertEqual(copied_file_response.json()['failed'], [])
        self.assertEqual(copied_folder_response.status_code, 200)
        self.assertEqual(copied_folder_response.json()['copied'][0]['name'], 'Source (copy)')
        self.assertEqual(copied_folder_response.json()['failed'], [])


if __name__ == '__main__':
    unittest.main()
