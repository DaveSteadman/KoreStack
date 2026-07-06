import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


HERE             = Path(__file__).resolve().parent
KORE_DATA_ROOT   = HERE.parent
COMMON_CODE_ROOT = KORE_DATA_ROOT / "CommonCode"

for path in (HERE, KORE_DATA_ROOT, COMMON_CODE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["KOREDATA_DATA_DIR"] = _TMP_DIR.name

from app import chroma_index  # noqa: E402
from app.database import (  # noqa: E402
    get_article_sentences,
    get_db_path,
    get_sentence,
    init_db,
    rebuild_sentence_index,
    upsert_article,
)


class ReferenceSentenceIndexTests(unittest.TestCase):
    def test_upsert_article_indexes_summary_and_prose_only(self) -> None:
        init_db()

        original_sync_article_sentences = chroma_index.sync_article_sentences
        chroma_index.sync_article_sentences = lambda article_id: 0
        try:
            article = upsert_article(
                title   = "Semantic Test",
                summary = "Summary sentence.",
                body    = (
                    "Lead paragraph one. Lead paragraph two?\n\n"
                    "== History ==\n\n"
                    "History sentence.\n\n"
                    "* Bullet list item.\n\n"
                    "<<<TABLE>>><table><tr><td>Tabular fact.</td></tr></table><<<ENDTABLE>>>\n\n"
                    "== Notes ==\n\n"
                    "Notes sentence should be skipped."
                ),
            )
        finally:
            chroma_index.sync_article_sentences = original_sync_article_sentences

        rows = get_article_sentences(int(article["id"]))

        self.assertEqual(
            [row["sentence_text"] for row in rows],
            [
                "Summary sentence.",
                "Lead paragraph one.",
                "Lead paragraph two?",
                "History sentence.",
            ],
        )
        self.assertTrue(all(row["locator"].startswith("reference/main/") for row in rows))

        conn = sqlite3.connect(str(get_db_path()))
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(sentences)").fetchall()}
            self.assertNotIn("sentence_text", cols)
        finally:
            conn.close()

    def test_rebuild_sentence_index_restores_missing_rows_for_article(self) -> None:
        init_db()

        original_sync_article_sentences = chroma_index.sync_article_sentences
        chroma_index.sync_article_sentences = lambda article_id: 0
        try:
            upsert_article(
                title   = "Rebuild Test",
                summary = "Summary first.",
                body    = "Body sentence one. Body sentence two.",
            )
        finally:
            chroma_index.sync_article_sentences = original_sync_article_sentences

        conn = sqlite3.connect(str(get_db_path()))
        try:
            conn.execute("DELETE FROM sentences WHERE article_id = 1")
            conn.commit()
        finally:
            conn.close()

        result   = rebuild_sentence_index(article_id=1)
        restored = get_article_sentences(1)
        single   = get_sentence(restored[0]["id"]) if restored else None

        self.assertGreaterEqual(result["rebuilt_sentences"], 1)
        self.assertEqual([row["sentence_text"] for row in restored], ["Summary first.", "Body sentence one.", "Body sentence two."])
        self.assertIsNotNone(single)


if __name__ == "__main__":
    unittest.main()
