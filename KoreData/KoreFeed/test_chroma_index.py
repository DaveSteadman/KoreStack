import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
KORE_DATA_ROOT = HERE.parent
COMMON_CODE_ROOT = KORE_DATA_ROOT / "CommonCode"

for path in (HERE, KORE_DATA_ROOT, COMMON_CODE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["KOREDATA_DATA_DIR"] = _TMP_DIR.name

from app import chroma_index
from app.database import delete_domain_db, get_db_path, insert_entry, init_db, rename_domain_db


class _FakeCollection:
    def __init__(self) -> None:
        self.upserts: list[dict] = []
        self.deletes: list[list[str]] = []
        self.query_response = {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }

    def upsert(self, ids, documents, metadatas) -> None:
        self.upserts.append(
            {"ids": list(ids), "documents": list(documents), "metadatas": list(metadatas)}
        )

    def delete(self, ids) -> None:
        self.deletes.append(list(ids))

    def count(self) -> int:
        return len(self.query_response["ids"][0])

    def query(self, query_texts, n_results) -> dict:
        return self.query_response


class _FakeClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class ChromaIndexTests(unittest.TestCase):
    def test_sync_pending_sentences_marks_rows_indexed(self) -> None:
        domain = "chroma_domain"
        init_db(domain)

        original_sync_entry_sentences = chroma_index.sync_entry_sentences
        chroma_index.sync_entry_sentences = lambda domain, entry_id: 0
        try:
            inserted = insert_entry(
                domain=domain,
                feed_name="Test Feed",
                headline="Headline one.",
                url="https://example.com/chroma-1",
                published="2026-07-02 14:00:00",
                metadata={},
                page_text="Body sentence one. Body sentence two.",
            )
        finally:
            chroma_index.sync_entry_sentences = original_sync_entry_sentences

        fake = _FakeCollection()
        original_get_collection = chroma_index._get_collection
        chroma_index._get_collection = lambda domain_name: fake
        try:
            synced = chroma_index.sync_pending_sentences(domain, batch_size=10)
        finally:
            chroma_index._get_collection = original_get_collection

        self.assertTrue(inserted)
        self.assertEqual(synced, 3)
        self.assertEqual(len(fake.upserts), 1)
        self.assertEqual(len(fake.upserts[0]["ids"]), 3)
        self.assertTrue(all(item.startswith("feeds/chroma_domain/") for item in fake.upserts[0]["ids"]))

        conn = sqlite3.connect(str(get_db_path(domain)))
        try:
            indexed_count = conn.execute(
                "SELECT COUNT(*) FROM sentences WHERE chroma_indexed_at IS NOT NULL AND chroma_indexed_at != ''"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(indexed_count, 3)

    def test_delete_sentence_ids_uses_domain_specific_locators(self) -> None:
        fake = _FakeCollection()
        original_get_collection = chroma_index._get_collection
        chroma_index._get_collection = lambda domain_name: fake
        try:
            deleted = chroma_index.delete_sentence_ids("DefenceNews", [7, 8, 9])
        finally:
            chroma_index._get_collection = original_get_collection

        self.assertEqual(deleted, 3)
        self.assertEqual(fake.deletes, [["feeds/DefenceNews/7", "feeds/DefenceNews/8", "feeds/DefenceNews/9"]])

    def test_rename_domain_db_moves_parallel_chroma_store(self) -> None:
        old_domain = "old_domain"
        new_domain = "new_domain"
        init_db(old_domain)

        old_chroma_path = chroma_index._domain_chroma_path(old_domain)
        old_chroma_path.mkdir(parents=True, exist_ok=True)
        (old_chroma_path / "marker.txt").write_text("ok", encoding="utf-8")

        fake_client = _FakeClient()
        safe_old = chroma_index._sanitize_domain(old_domain)
        chroma_index._CLIENTS[safe_old] = fake_client
        try:
            renamed = rename_domain_db(old_domain, new_domain)
        finally:
            chroma_index._CLIENTS.pop(safe_old, None)

        self.assertTrue(renamed)
        self.assertFalse(get_db_path(old_domain).exists())
        self.assertTrue(get_db_path(new_domain).exists())
        self.assertFalse(old_chroma_path.exists())
        self.assertTrue((chroma_index._domain_chroma_path(new_domain) / "marker.txt").exists())
        self.assertTrue(fake_client.closed)

    def test_delete_domain_db_removes_parallel_chroma_store(self) -> None:
        domain = "delete_domain"
        init_db(domain)

        chroma_path = chroma_index._domain_chroma_path(domain)
        chroma_path.mkdir(parents=True, exist_ok=True)
        (chroma_path / "marker.txt").write_text("ok", encoding="utf-8")

        fake_client = _FakeClient()
        safe_domain = chroma_index._sanitize_domain(domain)
        chroma_index._CLIENTS[safe_domain] = fake_client
        try:
            deleted = delete_domain_db(domain)
        finally:
            chroma_index._CLIENTS.pop(safe_domain, None)

        self.assertTrue(deleted)
        self.assertFalse(get_db_path(domain).exists())
        self.assertFalse(chroma_path.exists())
        self.assertTrue(fake_client.closed)

    def test_semantic_search_maps_chroma_hits_to_feed_results(self) -> None:
        domain = "semantic_domain"
        init_db(domain)

        original_sync_entry_sentences = chroma_index.sync_entry_sentences
        chroma_index.sync_entry_sentences = lambda domain, entry_id: 0
        try:
            insert_entry(
                domain=domain,
                feed_name="Semantic Feed",
                headline="Iron Dome upgrade",
                url="https://example.com/semantic-1",
                published="2026-07-02 14:30:00",
                metadata={},
                page_text="Iron Dome received a laser integration upgrade.",
            )
        finally:
            chroma_index.sync_entry_sentences = original_sync_entry_sentences

        fake = _FakeCollection()
        fake_store = Path(_TMP_DIR.name) / "semantic_domain_store"
        fake_store.mkdir(parents=True, exist_ok=True)
        fake.query_response = {
            "ids": [["feeds/semantic_domain/2"]],
            "documents": [["Iron Dome received a laser integration upgrade."]],
            "metadatas": [[{
                "entry_id": 1,
                "sentence_id": 2,
                "feed_name": "Semantic Feed",
                "headline": "Iron Dome upgrade",
                "published": "2026-07-02 14:30:00",
                "url": "https://example.com/semantic-1",
            }]],
            "distances": [[0.1234]],
        }

        original_get_collection = chroma_index._get_collection
        original_path_fn = chroma_index._domain_chroma_path
        chroma_index._get_collection = lambda _: fake
        chroma_index._domain_chroma_path = lambda _: fake_store
        try:
            results = chroma_index.semantic_search(domain, "laser integration", limit=10)
        finally:
            chroma_index._get_collection = original_get_collection
            chroma_index._domain_chroma_path = original_path_fn

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], 1)
        self.assertEqual(results[0]["sentence_id"], 2)
        self.assertEqual(results[0]["sentence_locator"], "feeds/semantic_domain/2")
        self.assertEqual(results[0]["headline"], "Iron Dome upgrade")
        self.assertIn("laser integration upgrade", results[0]["snippet"])


if __name__ == "__main__":
    unittest.main()
