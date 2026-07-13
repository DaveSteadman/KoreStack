# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreChat entry point.
#
# Run with:
#   python main.py
#
# Or via uvicorn directly, using the configured host and port from
# config/korestack_config.json.
# ====================================================================================================

from datetime import datetime
import logging
from pathlib import Path

import uvicorn

from app.config import cfg
from app.logutil import configure_service_logging
from app.logutil import get_service_log_path
from app.logutil import make_log_config

_W = 72


# ----------------------------------------------------------------------------------------------------
def _print_banner() -> None:
    now = datetime.now().strftime("%H:%M:%S")
    sep = "=" * _W

    def row(label: str, value: str) -> str:
        return f"  {label:<22} {value}"

    lines = [
        "",
        sep,
        f"  KORECHAT  [{now}]",
        sep,
        "",
        row("API:",       f"http://localhost:{cfg['port']}/"),
        row("Debug UI:",  f"http://localhost:{cfg['port']}/ui"),
        row("Events:",    f"http://localhost:{cfg['port']}/events/next"),
        row("Data dir:",  cfg["data_dir"]),
        row("Log level:", cfg["log_level"].upper()),
        "",
        sep,
        "",
    ]
    print("\n".join(lines))


# ----------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    configure_service_logging("korechat", cfg["log_level"])
    try:
        _log_path = get_service_log_path("korechat")
        logging.getLogger("korechat.service").info("starting host=%s port=%s log=%s", cfg["host"], int(cfg["port"]), _log_path)
        _print_banner()
        uvicorn.run(
            "app.server:app",
            host       = cfg["host"],
            port       = int(cfg["port"]),
            access_log = False,
            log_level  = cfg["log_level"],
            log_config = make_log_config("korechat", cfg["log_level"]),
        )
    except Exception:
        logging.getLogger("korechat.service").exception("startup failed")
        raise
