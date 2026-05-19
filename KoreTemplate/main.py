# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Root launcher for KoreTemplate.
#
# This service has two purposes:
#   1. Test area  — serves UIElements2 docs at /ui-elements-2/docs/ so element2.html
#                   and future reference pages are visible through a real HTTP server
#                   (required because font/asset paths are absolute: /ui-elements-2/...)
#   2. Template   — minimal copy-from baseline for new KoreStack services.
#                   Copy the whole KoreTemplate/ folder, rename, change the port.
#
# Run with:  python main.py
#            KORETEMPLATE_PORT=8010 python main.py
# ====================================================================================================
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import uvicorn

PORT = int(os.environ.get("KORETEMPLATE_PORT", 8010))
_W   = 60


def _print_banner() -> None:
    now = datetime.now().strftime("%H:%M:%S")
    sep = "=" * _W

    def row(label: str, value: str) -> str:
        return f"  {label:<20} {value}"

    lines = [
        "",
        sep,
        f"  KORETEMPLATE  [{now}]",
        sep,
        "",
        row("WebUI:", f"http://localhost:{PORT}/"),
        row("Element2 ref:", f"http://localhost:{PORT}/ui/element2.html"),
        row("Status:", f"http://localhost:{PORT}/status"),
        "",
        sep,
        "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    _print_banner()
    uvicorn.run(
        "app.server:app",
        host="0.0.0.0",
        port=PORT,
        reload=True,
        reload_dirs=[str(Path(__file__).parent)],
        log_level="info",
    )
