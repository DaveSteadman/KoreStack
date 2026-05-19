# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Root launcher for KoreComms — the external messaging bridge for KoreStack.
#
# Loads configuration, prints a startup banner showing the WebUI port, and starts
# the FastAPI application under uvicorn.  Default port: 8900 (env: KORECOMMS_PORT).
#
# Run with:  python ./main.py
#
# Related modules:
#   - app/server.py   -- FastAPI application and all routes
#   - app/config.py   -- configuration loading
#   - app/poller.py   -- starts the background polling thread
# ====================================================================================================
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import uvicorn

from app.config import cfg
from app.logutil import make_log_config

_W = 72


def _print_banner() -> None:
    now = datetime.now().strftime("%H:%M:%S")
    sep = "=" * _W

    def row(label: str, value: str) -> str:
        return f"  {label:<22} {value}"

    lines = [
        "",
        sep,
        f"  KORECOMMS  [{now}]",
        sep,
        "",
        row("WebUI:", f"http://localhost:{cfg['port']}/"),
        row("Agent API:", f"http://localhost:{cfg['port']}/api/"),
        row("KoreChat:", cfg["korechat_url"]),
        row("Poll interval:", f"{cfg['poll_interval']}s"),
        row("Data dir:", cfg["data_dir"]),
        row("Log level:", cfg["log_level"].upper()),
        "",
        sep,
        "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    _log_path = Path(cfg["data_dir"]) / "korecomms.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _print_banner()
    uvicorn.run(
        "app.server:app",
        host=cfg["host"],
        port=int(cfg["port"]),
        log_level=cfg["log_level"],
        log_config=make_log_config(_log_path),
    )
