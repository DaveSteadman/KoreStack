# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Tests for KoreLibrary write helpers in the KoreDataGateway.
# Verifies the gateway issues the expected HTTP methods and payloads for book edits.
# ====================================================================================================

import sys
from pathlib import Path
import unittest


SERVICE_ROOT = Path(__file__).resolve().parents[1]
COMMON_CODE_ROOT = Path(__file__).resolve().parents[2] / "CommonCode"
if str(COMMON_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_CODE_ROOT))
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.gateway_library import repair_library_book_anchors
from app.gateway_library import update_library_book


class _Response:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None, float]] = []

    async def patch(self, path: str, json: dict | None = None, timeout: float = 15.0):
        self.calls.append(("PATCH", path, json, timeout))
        return _Response(200, {"ok": True, "path": path, "json": json})

    async def post(self, path: str, timeout: float = 15.0):
        self.calls.append(("POST", path, None, timeout))
        return _Response(200, {"ok": True, "path": path})


class GatewayLibraryWriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_library_book_sends_patch_payload(self) -> None:
        client = _Client()

        result = await update_library_book(
            client,
            book_id="sciencehistory:6",
            title="History of Science",
            body="# Chapter 1\n\nFixed body text.",
            notes="TOC repaired",
        )

        self.assertEqual(client.calls[0][0], "PATCH")
        self.assertEqual(client.calls[0][1], "/books/sciencehistory%3A6")
        self.assertEqual(client.calls[0][2], {
            "title": "History of Science",
            "body": "# Chapter 1\n\nFixed body text.",
            "notes": "TOC repaired",
        })
        self.assertTrue(result["ok"])

    async def test_repair_library_book_anchors_sends_post(self) -> None:
        client = _Client()

        result = await repair_library_book_anchors(client, book_id="sciencehistory:6")

        self.assertEqual(client.calls[0][0], "POST")
        self.assertEqual(client.calls[0][1], "/books/sciencehistory%3A6/repair-anchors")
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()