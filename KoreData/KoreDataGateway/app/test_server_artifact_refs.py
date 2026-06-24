import sys
import tempfile
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

    async def test_rag_databases_enriched_preserves_base_navigation_on_partial_info(self) -> None:
        class _Response:
            def __init__(self, status_code: int, payload: object) -> None:
                self.status_code = status_code
                self._payload    = payload

            def json(self) -> object:
                return self._payload

        class _Client:
            async def get(self, path: str):
                if path == "/databases":
                    return _Response(
                        200,
                        [{
                            "id":         "alpha",
                            "navigation": {"type": "hansard"},
                            "managed_by": "ingestor",
                        }],
                    )
                if path == "/databases/alpha/info":
                    return _Response(
                        200,
                        {
                            "id":            "alpha",
                            "db_size_bytes": 1024,
                        },
                    )
                raise AssertionError(f"unexpected path: {path}")

        original_rag_client = server._rag_client
        try:
            server._rag_client = _Client()
            enriched = await server._rag_databases_enriched()
        finally:
            server._rag_client = original_rag_client

        self.assertEqual(len(enriched), 1)
        self.assertEqual(enriched[0]["id"], "alpha")
        self.assertEqual(enriched[0]["navigation"], {"type": "hansard"})
        self.assertEqual(enriched[0]["db_size_bytes"], 1024)

    async def test_rag_databases_enriched_falls_back_to_local_db_size(self) -> None:
        class _Response:
            def __init__(self, status_code: int, payload: object) -> None:
                self.status_code = status_code
                self._payload    = payload

            def json(self) -> object:
                return self._payload

        class _Client:
            async def get(self, path: str):
                if path == "/databases":
                    return _Response(200, [{"id": "alpha"}])
                if path == "/databases/alpha/info":
                    return _Response(200, {"id": "alpha", "db_size_bytes": None})
                raise AssertionError(f"unexpected path: {path}")

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db_dir   = base_dir / "RAG" / "databases" / "alpha"
            db_path  = db_dir / "alpha.db"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path.write_bytes(b"x" * 4096)

            original_rag_client        = server._rag_client
            original_get_koredata_dir  = server.get_koredata_dir
            try:
                server._rag_client       = _Client()
                server.get_koredata_dir  = lambda: base_dir
                enriched                 = await server._rag_databases_enriched()
            finally:
                server._rag_client       = original_rag_client
                server.get_koredata_dir  = original_get_koredata_dir

        self.assertEqual(len(enriched), 1)
        self.assertEqual(enriched[0]["db_size_bytes"], 4096)

    def test_rag_processing_scripts_include_schedule_and_last_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir        = Path(tmp)
            script_dir      = base_dir / "RAG" / "databases" / "alpha"
            descriptor_path = script_dir / "alpha.json"
            script_dir.mkdir(parents=True, exist_ok=True)
            (script_dir / "ingest.py").write_text("print('ok')\n", encoding="utf-8")
            descriptor_path.write_text(
                (
                    "{\n"
                    '  "display_name": "Alpha",\n'
                    '  "managed_by": "ingestor",\n'
                    '  "schedule": "weekly",\n'
                    '  "sync": {"last_run": "2026-06-16 21:30:45", "last_ingest_completed_at": "2026-06-16 21:42:03", "last_date_ingested": "2026-06-15", "status": "complete"}\n'
                    "}\n"
                ),
                encoding="utf-8",
            )

            original_get_koredata_dir = server.get_koredata_dir
            try:
                server.get_koredata_dir = lambda: base_dir
                scripts = server._rag_processing_scripts({"alpha"})
            finally:
                server.get_koredata_dir = original_get_koredata_dir

        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]["id"], "alpha")
        self.assertEqual(scripts[0]["schedule"], "weekly")
        self.assertEqual(scripts[0]["last_run"], "2026-06-16 21:30:45")
        self.assertTrue(scripts[0]["has_database"])

    def test_normalize_rag_processing_schedule_rejects_unknown_values(self) -> None:
        self.assertEqual(server._normalize_rag_processing_schedule("manual"), "manual")
        self.assertEqual(server._normalize_rag_processing_schedule("daily"), "daily")
        self.assertEqual(server._normalize_rag_processing_schedule("monthly"), "monthly")
        self.assertEqual(server._normalize_rag_processing_schedule("yearly"), "manual")


if __name__ == "__main__":
    unittest.main()
