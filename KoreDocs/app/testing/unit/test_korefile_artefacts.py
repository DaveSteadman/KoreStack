# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# test korefile artefacts module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Primary types: KoreFileArtefactTests.
# Function inventory:
# - setUp: Implements the setUp operation for this module.
# - tearDown: Implements the tearDown operation for this module.
# - test_metadata_query_supports_nested_exact_array_and_range_conditions: Implements the test metadata query supports nested exact array and range conditions operation for this module.
# - test_stable_artifact_id_and_history_survive_rename_and_update: Implements the test stable artifact id and history survive rename and update operation for this module.
# - test_metadata_patch_merges_nested_fields_without_replacing_the_artefact: Implements the test metadata patch merges nested fields without replacing the artefact operation for this module.
# - test_koredoc_embeds_json_header_without_a_metadata_sidecar: Implements the test koredoc embeds json header without a metadata sidecar operation for this module.
# ====================================================================================================

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from KoreDocs.app.documents.korefile import service as korefile


class KoreFileArtefactTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name) / 'datauser'
        korefile.configure(self.root)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_metadata_query_supports_nested_exact_array_and_range_conditions(self) -> None:
        korefile.create_file(1, 'gb-2025.koredoc', '# GB market', {
            'artefact_type': 'market_analysis',
            'geography':     {'country': 'GB'},
            'period':        {'year': 2025},
            'tags':          ['automotive', 'reviewed'],
        })
        korefile.create_file(1, 'us-2024.koredoc', '# US market', {
            'artefact_type': 'market_analysis',
            'geography':     {'country': 'US'},
            'period':        {'year': 2024},
        })

        matches = korefile.search_metadata({
            'artefact_type':     'market_analysis',
            'geography.country': 'GB',
            'period.year':       {'gte': 2025},
            'tags':              {'contains': 'automotive'},
        })

        self.assertEqual([item['name'] for item in matches], ['gb-2025.koredoc'])

    def test_stable_artifact_id_and_history_survive_rename_and_update(self) -> None:
        created = korefile.create_file(1, 'draft.koredoc', '# Version one', {'status': 'draft'})
        artifact_id = created['artifact_id']
        renamed = korefile.rename_file(created['id'], 'final.koredoc')
        updated = korefile.update_file(renamed['id'], '# Version two', {'status': 'published'})

        self.assertEqual(updated['artifact_id'], artifact_id)
        history = korefile.list_history(updated['id'])
        self.assertEqual(len(history), 2)
        original = korefile.get_history_revision(updated['id'], history[-1]['revision'])
        self.assertEqual(original['body_content'], '# Version one')
        self.assertEqual(original['metadata'], {'status': 'draft'})

    def test_metadata_patch_merges_nested_fields_without_replacing_the_artefact(self) -> None:
        created = korefile.create_file(1, 'analysis.koredoc', '# Analysis', {
            'geography': {'country': 'GB'},
            'producer':  {'service': 'KoreAgent'},
        })

        updated = korefile.update_file(created['id'], metadata_patch={
            'geography': {'region': 'Europe'},
            'status':    'reviewed',
        })

        self.assertEqual(updated['metadata'], {
            'geography': {'country': 'GB', 'region': 'Europe'},
            'producer':  {'service': 'KoreAgent'},
            'status':    'reviewed',
        })

    def test_koredoc_embeds_json_header_without_a_metadata_sidecar(self) -> None:
        created = korefile.create_file(1, 'portable.koredoc', '# Portable', {'tags': ['primes']})
        document_path = self.root / 'portable.koredoc'

        serialized = document_path.read_text(encoding='utf-8')

        self.assertTrue(serialized.startswith('---koredocs-json\n'))
        self.assertIn(created['artifact_id'], serialized)
        self.assertIn('"tags": [', serialized)
        self.assertFalse((self.root / '.portable.koredoc.koremeta.json').exists())
