"""KoreLibrary CORS is limited to loopback browser origins."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(os.environ["KORESTACK_ROOT"])
DATA_ROOT = ROOT / "KoreData"
LIBRARY_ROOT = DATA_ROOT / "KoreLibrary"
for path in (LIBRARY_ROOT, DATA_ROOT / "CommonCode"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.server import app


def test_library_cors_allows_loopback_and_rejects_external_origins() -> None:
    client = TestClient(app)
    for origin in ("http://localhost:29600", "http://127.0.0.1:29600", "http://[::1]:29600"):
        response = client.options(
            "/api/books",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin

    response = client.options(
        "/api/books",
        headers={"Origin": "https://example.com", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
