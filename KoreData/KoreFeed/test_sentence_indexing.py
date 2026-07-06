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

from app.database import (
    backfill_sentence_index,
    delete_entry,
    get_db_path,
    get_entry_sentences,
    get_sentence,
    init_db,
    insert_entry,
    rebuild_sentence_index,
)
from app import chroma_index


class FeedSentenceIndexTests(unittest.TestCase):
    def test_insert_entry_indexes_sentences(self) -> None:
        domain = "sentence_index_domain"
        init_db(domain)

        original_sync_entry_sentences = chroma_index.sync_entry_sentences
        chroma_index.sync_entry_sentences = lambda domain, entry_id: 0
        try:
            inserted = insert_entry(
                domain=domain,
                feed_name="Test Feed",
                headline="Headline one.",
                url="https://example.com/article-1",
                published="2026-07-02 12:00:00",
                metadata={"author": "Tester"},
                page_text="First body sentence. Second body sentence!",
            )
        finally:
            chroma_index.sync_entry_sentences = original_sync_entry_sentences
        sentences = get_entry_sentences(domain, 1)

        self.assertTrue(inserted)
        self.assertEqual([row["sentence_index"] for row in sentences], [0, 1, 2])
        self.assertEqual(sentences[0]["source_field"], "headline")
        self.assertEqual(sentences[1]["source_field"], "page_text")
        self.assertEqual(sentences[2]["sentence_text"], "Second body sentence!")

        conn = sqlite3.connect(str(get_db_path(domain)))
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(sentences)").fetchall()}
            self.assertNotIn("sentence_text", cols)
        finally:
            conn.close()

    def test_deleted_entry_hides_sentences(self) -> None:
        domain = "sentence_delete_domain"
        init_db(domain)

        original_sync_entry_sentences = chroma_index.sync_entry_sentences
        original_delete_sentence_ids = chroma_index.delete_sentence_ids
        chroma_index.sync_entry_sentences = lambda domain, entry_id: 0
        chroma_index.delete_sentence_ids = lambda domain, sentence_ids: 0
        try:
            insert_entry(
                domain=domain,
                feed_name="Test Feed",
                headline="Headline one.",
                url="https://example.com/article-2",
                published="2026-07-02 12:30:00",
                metadata={},
                page_text="Body sentence.",
            )
        finally:
            chroma_index.sync_entry_sentences = original_sync_entry_sentences
        before_delete = get_entry_sentences(domain, 1)

        try:
            deleted = delete_entry(domain, 1)
        finally:
            chroma_index.delete_sentence_ids = original_delete_sentence_ids
        after_delete = get_sentence(domain, before_delete[0]["id"])

        self.assertTrue(deleted)
        self.assertGreater(len(before_delete), 0)
        self.assertIsNone(after_delete)

    def test_backfill_sentence_index_reports_zero_when_up_to_date(self) -> None:
        domain = "sentence_backfill_domain"
        init_db(domain)

        original_sync_entry_sentences = chroma_index.sync_entry_sentences
        chroma_index.sync_entry_sentences = lambda domain, entry_id: 0
        try:
            insert_entry(
                domain=domain,
                feed_name="Test Feed",
                headline="Headline one.",
                url="https://example.com/article-3",
                published="2026-07-02 13:00:00",
                metadata={},
                page_text="Body sentence one. Body sentence two.",
            )
        finally:
            chroma_index.sync_entry_sentences = original_sync_entry_sentences

        result = backfill_sentence_index(domain)

        self.assertEqual(result["sentences_added"], 0)
        self.assertGreaterEqual(result["sentence_count"], 1)

    def test_rebuild_sentence_index_restores_missing_rows_for_entry(self) -> None:
        domain = "sentence_rebuild_domain"
        init_db(domain)

        original_sync_entry_sentences = chroma_index.sync_entry_sentences
        chroma_index.sync_entry_sentences = lambda domain, entry_id: 0
        try:
            insert_entry(
                domain=domain,
                feed_name="Test Feed",
                headline="Headline one.",
                url="https://example.com/article-4",
                published="2026-07-02 13:30:00",
                metadata={},
                page_text="Body sentence one. Body sentence two.",
            )
        finally:
            chroma_index.sync_entry_sentences = original_sync_entry_sentences

        conn = sqlite3.connect(str(get_db_path(domain)))
        try:
            conn.execute("DELETE FROM sentences WHERE entry_id = 1")
            conn.commit()
        finally:
            conn.close()

        result = rebuild_sentence_index(domain, entry_id=1)
        rows = get_entry_sentences(domain, 1)

        self.assertEqual(result["rebuilt_entries"], 1)
        self.assertGreaterEqual(result["rebuilt_sentences"], 1)
        self.assertGreaterEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
