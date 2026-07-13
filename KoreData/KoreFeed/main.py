# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Root launcher for KoreFeed sub-service.
#
# Prints a startup status showing domain count and feed count, then starts the
# FastAPI app under uvicorn.
#
# Related modules:
#   - app/server.py       -- FastAPI application and feed API routes
#   - app/config.py       -- cfg (host, port, data_dir)
#   - app/database.py     -- feed article storage
#   - app/ingest.py       -- background RSS polling scheduler
#   - app/feed_manager.py -- JSON feed configuration file I/O
#   - CommonCode/         -- shared logutil, config, compress
# ====================================================================================================
import sys
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "CommonCode"))

import logutil
import uvicorn
from datetime import datetime
from app.config import cfg
from app.feed_manager import load_feeds

_W = 100  # banner width

def _print_status() -> None:
    now = datetime.now().strftime("%H:%M:%S")
    feeds = load_feeds()
    n_feeds = len(feeds)
    domains = sorted({f["domain"] for f in feeds})
    n_domains = len(domains)
    host = cfg["host"]
    port = cfg["port"]
    data_dir = cfg["data_dir"]
    log_level = cfg["log_level"].upper()

    sep = "=" * _W
    def row(label: str, value: str) -> str:
        return f"{label:<20} {value}"

    lines = [
        "",
        sep,
        f"MINIFEED STATUS  [{now}]",
        sep,
        "",
        row("Host:", f"http://{host}:{port}/"),
        row("Web UI:", f"http://localhost:{port}/"),
        row("Data dir:", data_dir),
        row("Log level:", log_level),
        row("Domains:", str(n_domains) + (f"  ({', '.join(domains)})" if domains else "")),
        row("Feeds:", str(n_feeds)),
        "",
        sep,
        "",
    ]
    print("\n".join(lines))

if __name__ == "__main__":
    logutil.configure_service_logging("korefeed", cfg["log_level"])
    try:
        _DATA_DIR = Path(cfg["data_dir"])
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        logging.getLogger("korefeed.service").info("starting host=%s port=%s data_dir=%s", cfg["host"], cfg["port"], _DATA_DIR)
        _print_status()
        uvicorn.run(
            "app.server:app",
            host       = cfg["host"],
            port       = cfg["port"],
            access_log = False,
            log_level  = cfg["log_level"],
            log_config = logutil.make_log_config("korefeed", cfg["log_level"]),
            reload     = False,
        )
    except Exception:
        logging.getLogger("korefeed.service").exception("startup failed")
        raise
