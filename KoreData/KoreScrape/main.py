# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Entry point for KoreData/KoreScrape.
# Bootstraps the package application or utility from the command line.
# ====================================================================================================

import sys
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "CommonCode"))

import logutil
import uvicorn
from app.config import cfg

_W = 80


def _print_banner() -> None:
    now      = datetime.now().strftime("%H:%M:%S")
    host     = cfg["host"]
    port     = cfg["port"]
    data_dir = cfg["data_dir"]
    log_lvl  = cfg["log_level"].upper()

    sep = "=" * _W

    def row(label: str, value: str) -> str:
        return f"  {label:<22} {value}"

    lines = [
        "",
        sep,
        f"  KORESCRAPE  [{now}]",
        sep,
        "",
        row("Host:",        f"http://{host}:{port}/"),
        row("Data dir:",    data_dir),
        row("Log level:",   log_lvl),
        row("Index status:", "Initialising in background"),
        "",
        sep,
        "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    logutil.configure_service_logging("korescrape", cfg["log_level"])
    try:
        data_dir  = Path(cfg["data_dir"])
        data_dir.mkdir(parents=True, exist_ok=True)
        logging.getLogger("korescrape.service").info("starting host=%s port=%s data_dir=%s", cfg["host"], cfg["port"], data_dir)
        _print_banner()
        uvicorn.run(
            "app.server:app",
            host       = cfg["host"],
            port       = cfg["port"],
            access_log = False,
            log_level  = cfg["log_level"],
            log_config = logutil.make_log_config("korescrape", cfg["log_level"]),
        )
    except Exception:
        logging.getLogger("korescrape.service").exception("startup failed")
        raise
