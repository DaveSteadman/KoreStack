import tempfile
import unittest
from pathlib import Path
import sys

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_COMMON = _HERE.parent / "CommonCode"
if str(_COMMON) not in sys.path:
    sys.path.insert(0, str(_COMMON))

from app import chroma_index
from app import database


class LibrarySentenceIndexingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir        = tempfile.TemporaryDirectory()
        self.original_cfg   = dict(database.cfg)
        self.original_dir   = database.DATA_DIR
        self.original_db    = database._DB_PATH
        self.original_sync_book_sentences = chroma_index.sync_book_sentences
        self.original_delete_sentence_ids = chroma_index.delete_sentence_ids
        database.cfg["data_dir"] = self.tmp_dir.name
        database.DATA_DIR        = Path(self.tmp_dir.name)
        database._DB_PATH        = database.DATA_DIR / "library.db"
        chroma_index.sync_book_sentences = lambda catalog, book_id: 0
        chroma_index.delete_sentence_ids = lambda catalog, sentence_ids: 0

    def tearDown(self) -> None:
        database.cfg.clear()
        database.cfg.update(self.original_cfg)
        database.DATA_DIR = self.original_dir
        database._DB_PATH = self.original_db
        chroma_index.sync_book_sentences = self.original_sync_book_sentences
        chroma_index.delete_sentence_ids = self.original_delete_sentence_ids
        self.tmp_dir.cleanup()

    def test_add_book_indexes_sentences(self) -> None:
        book = database.add_book(
            title    = "Alpha title",
            body     = "First body sentence. Second body sentence!",
            author   = "Author",
            catalog  = "local",
        )
        rows = database.get_book_sentences(book["route_id"])
        self.assertEqual([row["sentence_index"] for row in rows], [0, 1, 2])
        self.assertEqual(rows[0]["source_field"], "title")
        self.assertEqual(rows[1]["source_field"], "body")
        self.assertEqual(rows[2]["sentence_text"], "Second body sentence!")

    def test_update_book_body_rebuilds_sentences(self) -> None:
        book = database.add_book(
            title   = "Alpha title",
            body    = "Old body sentence.",
            catalog = "local",
        )
        database.update_book_body(book["route_id"], "New sentence one. New sentence two.")
        rows = database.get_book_sentences(book["route_id"])
        self.assertEqual([row["sentence_text"] for row in rows], ["Alpha title", "New sentence one.", "New sentence two."])


if __name__ == "__main__":
    unittest.main()
