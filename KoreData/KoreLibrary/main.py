# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Root launcher for KoreLibrary sub-service.
#
# Starts the FastAPI app under uvicorn.
#
# Related modules:
#   - app/server.py    -- FastAPI application and book catalog API routes
#   - app/config.py    -- cfg (host, port, data_dir)
#   - app/database.py  -- book catalog storage (SQLite + FTS5); get_status()
#   - CommonCode/      -- shared logutil, config
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
    host = cfg["host"]
    port = cfg["port"]
    data_dir = cfg["data_dir"]
    log_level = cfg["log_level"].upper()

    sep = "=" * _W

    def row(label: str, value: str) -> str:
        return f"  {label:<22} {value}"

    lines = [
        "",
        sep,
        f"  KORELIBRARY  [{now}]",
        sep,
        "",
        row("Host:", f"http://{host}:{port}/"),
        row("Data dir:", data_dir),
        row("Log level:", log_level),
        row("Catalog status:", "Initialising in background"),
        "",
        sep,
        "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    logutil.configure_service_logging("korelibrary", cfg["log_level"])
    try:
        _DATA_DIR = Path(cfg["data_dir"])
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        logging.getLogger("korelibrary.service").info("starting host=%s port=%s data_dir=%s", cfg["host"], cfg["port"], _DATA_DIR)
        _print_banner()
        uvicorn.run(
            "app.server:app",
            host       = cfg["host"],
            port       = cfg["port"],
            access_log = False,
            log_level  = cfg["log_level"],
            log_config = logutil.make_log_config("korelibrary", cfg["log_level"]),
        )
    except Exception:
        logging.getLogger("korelibrary.service").exception("startup failed")
        raise
