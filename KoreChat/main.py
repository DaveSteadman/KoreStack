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
from pathlib import Path

import uvicorn

from app.config import cfg
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
    _log_path = Path(cfg["data_dir"]) / "korechat.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _print_banner()
    uvicorn.run(
        "app.server:app",
        host       = cfg["host"],
        port       = int(cfg["port"]),
        log_level  = cfg["log_level"],
        log_config = make_log_config(_log_path),
    )
