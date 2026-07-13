# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Root launcher for KoreReference sub-service.
#
# Prints a startup banner showing the service URL and article count, initialises
# the SQLite database, then starts the FastAPI app under uvicorn.
#
# Related modules:
#   - app/server.py    -- FastAPI application and article API routes
#   - app/config.py    -- cfg (host, port, data_dir)
#   - app/database.py  -- init_db() and article storage
#   - CommonCode/      -- shared logutil, config, compress, dbutil
# ====================================================================================================
import sys
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "CommonCode"))

import logutil
import uvicorn
from datetime import datetime
from app.config import cfg

_W = 80


def _print_banner() -> None:
    now = datetime.now().strftime("%H:%M:%S")
    sep = "=" * _W

    def row(label: str, value: str) -> str:
        return f"  {label:<24} {value}"

    lines = [
        "",
        sep,
        f"  KOREREFERENCE  [{now}]",
        sep,
        "",
        row("Host:", f"http://{cfg['host']}:{cfg['port']}/"),
        row("Data dir:", cfg["data_dir"]),
        row("Log level:", cfg["log_level"].upper()),
        "",
        sep,
        "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    logutil.configure_service_logging("korereference", cfg["log_level"])
    try:
        _DATA_DIR = Path(cfg["data_dir"])
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        logging.getLogger("korereference.service").info("starting host=%s port=%s data_dir=%s", cfg["host"], cfg["port"], _DATA_DIR)
        _print_banner()
        uvicorn.run(
            "app.server:app",
            host       = cfg["host"],
            port       = cfg["port"],
            access_log = False,
            log_level  = cfg["log_level"],
            log_config = logutil.make_log_config("korereference", cfg["log_level"]),
        )
    except Exception:
        logging.getLogger("korereference.service").exception("startup failed")
        raise
