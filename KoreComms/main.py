# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Root launcher for KoreComms — the external messaging bridge for KoreStack.
#
# Loads configuration, prints a startup banner showing the configured WebUI URL, and starts
# the FastAPI application under uvicorn.
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
import logging

import uvicorn

from app.config import cfg
from KoreCommon.service_logging import configure_service_logging
from KoreCommon.service_logging import make_service_log_config

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
    _log_path = configure_service_logging("korecomms", cfg["log_level"])
    _logger = logging.getLogger("korecomms.service")
    try:
        _logger.info("starting host=%s port=%s log=%s", cfg["host"], cfg["port"], _log_path)
        _print_banner()
        uvicorn.run(
            "app.server:app",
            host=cfg["host"],
            port=int(cfg["port"]),
            log_level=cfg["log_level"],
            log_config=make_service_log_config("korecomms", cfg["log_level"]),
        )
    except Exception:
        _logger.exception("startup failed")
        raise
    finally:
        _logger.info("shutdown complete")
