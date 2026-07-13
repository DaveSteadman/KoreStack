# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Root launcher for KoreRAG sub-service.
#
# Prints a startup banner showing total chunk count and SQLite database size, then
# starts the FastAPI app under uvicorn.
#
# Related modules:
#   - app/server.py    -- FastAPI application and chunk/search API routes
#   - app/config.py    -- cfg (host, port, data_dir)
#   - app/database.py  -- chunk storage (SQLite + FTS5); get_status()
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
from app.registry import list_databases

_W = 80


def _print_banner() -> None:
    now = datetime.now().strftime("%H:%M:%S")
    dbs = list_databases()
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
        f"  KORERAG  [{now}]",
        sep,
        "",
        row("Host:", f"http://{host}:{port}/"),
        row("Data dir:", data_dir),
        row("Log level:", log_level),
        "",
        "  Databases:",
    ]
    for db_info in dbs:
        lines.append(f"    {db_info['display_name']:<28} initialising")
    lines += ["", sep, ""]
    print("\n".join(lines))


if __name__ == "__main__":
    logutil.configure_service_logging("korerag", cfg["log_level"])
    try:
        _DATA_DIR = Path(cfg["data_dir"])
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        logging.getLogger("korerag.service").info("starting host=%s port=%s data_dir=%s", cfg["host"], cfg["port"], _DATA_DIR)
        _print_banner()
        uvicorn.run(
            "app.server:app",
            host       = cfg["host"],
            port       = cfg["port"],
            access_log = False,
            log_level  = cfg["log_level"],
            log_config = logutil.make_log_config("korerag", cfg["log_level"]),
            reload     = False,
        )
    except Exception:
        logging.getLogger("korerag.service").exception("startup failed")
        raise
