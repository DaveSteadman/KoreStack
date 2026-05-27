import tempfile
import unittest
from pathlib import Path

from KoreDocs.app import korefile


class KoreFileDeleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / 'korefile.db'
        korefile.configure(self.db_path)
        korefile.init_db()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_delete_folder_recursive_removes_nested_files_and_folders(self) -> None:
        parent = korefile.create_folder('feeds2', 1)
        child = korefile.create_folder('archive', parent['id'])
        korefile.create_file(parent['id'], 'alpha.koredoc', '# Alpha', {})
        korefile.create_file(child['id'], 'beta.koredoc', '# Beta', {})
        current_parent = korefile.get_folder_by_path('/feeds2')

        deleted = korefile.delete_folder(parent['id'], expected_revision=current_parent['revision'], recursive=True)

        self.assertTrue(deleted)
        self.assertIsNone(korefile.get_folder_by_path('/feeds2'))
        self.assertIsNone(korefile.get_folder_by_path('/feeds2/archive'))
        folders = korefile.list_folders()
        self.assertEqual(len(folders), 1)
        self.assertEqual(folders[0]['id'], 1)
        self.assertEqual(folders[0]['path'], '/')

    def test_delete_folder_non_recursive_still_rejects_non_empty_folder(self) -> None:
        parent = korefile.create_folder('feeds2', 1)
        korefile.create_file(parent['id'], 'alpha.koredoc', '# Alpha', {})

        with self.assertRaises(Exception) as exc:
            korefile.delete_folder(parent['id'], expected_revision=parent['revision'])

        self.assertIn('FOREIGN KEY constraint failed', str(exc.exception))