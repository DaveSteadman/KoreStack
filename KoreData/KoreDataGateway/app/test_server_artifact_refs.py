import sys
from pathlib import Path
import unittest


SERVICE_ROOT = Path(__file__).resolve().parents[1]
COMMON_CODE_ROOT = Path(__file__).resolve().parents[2] / "CommonCode"
if str(COMMON_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_CODE_ROOT))
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app import server  # noqa: E402


class ArtifactRefTests(unittest.IsolatedAsyncioTestCase):
    def test_map_feed_entry_includes_artifact_ref(self) -> None:
        mapped = server._map_feed_entry(
            {
                "domain": "tech",
                "id": 42,
                "headline": "Drone News",
                "page_text": "A" * 500,
            }
        )

        self.assertEqual(mapped["artifact_ref"], "feed_entry|domain=tech|id=42")
        self.assertEqual(mapped["snippet"], "A" * 300)

    def test_parse_reference_artifact_ref_restores_title(self) -> None:
        mapped = server._map_ref_article({"title": "History of Flight/Drone?", "summary": "Test"})

        self.assertEqual(
            mapped["artifact_ref"],
            "reference_article|title=History%20of%20Flight%2FDrone%3F",
        )
        kind, parts = server._parse_artifact_ref(mapped["artifact_ref"])
        self.assertEqual(kind, "reference_article")
        self.assertEqual(parts["title"], "History of Flight/Drone?")

    async def test_get_full_text_dispatches_feed_ref(self) -> None:
        original = server.koredata_get_feed_entry

        async def fake_get_feed_entry(domain: str, entry_id: int) -> dict:
            return {"domain": domain, "entry_id": entry_id, "page_text": "full"}

        server.koredata_get_feed_entry = fake_get_feed_entry
        try:
            result = await server.koredata_get_full_text("feed_entry|domain=tech|id=42")
        finally:
            server.koredata_get_feed_entry = original

        self.assertEqual(result["domain"], "tech")
        self.assertEqual(result["entry_id"], 42)
        self.assertEqual(result["page_text"], "full")

    async def test_get_full_text_rejects_library_book(self) -> None:
        result = await server.koredata_get_full_text("library_book|book_id=sciencehistory%3A6")

        self.assertIn("chunked by design", result["error"])
        self.assertIn("sciencehistory:6", result["error"])

    async def test_api_full_text_delegates_to_ref_dispatcher(self) -> None:
        original = server.koredata_get_full_text

        async def fake_get_full_text(refid: str) -> dict:
            return {"artifact_ref": refid, "body": "full"}

        server.koredata_get_full_text = fake_get_full_text
        try:
            result = await server.api_full_text(server._FullTextRequest(refid="feed_entry|domain=tech|id=42"))
        finally:
            server.koredata_get_full_text = original

        self.assertEqual(result["artifact_ref"], "feed_entry|domain=tech|id=42")
        self.assertEqual(result["body"], "full")


if __name__ == "__main__":
    unittest.main()