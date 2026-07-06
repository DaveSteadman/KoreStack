# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Root launcher for KoreGraph sub-service.
#
# Prints a startup banner showing entity count, relation count, and listen address,
# then starts the FastAPI app under uvicorn.
#
# Related modules:
#   - app/server.py    -- FastAPI application, REST API, UI routes, MCP
#   - app/config.py    -- cfg (host, port, data_dir)
#   - app/database.py  -- SQLite schema, CRUD, graph traversal
#   - CommonCode/      -- shared logutil, config, compress, dbutil
# ====================================================================================================
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "CommonCode"))

import logutil
import uvicorn
from datetime import datetime
from app.config import cfg
from config import get_suite_datacontrol_dir

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
        f"  KOREGRAPH  [{now}]",
        sep,
        "",
        row("Host:", f"http://{host}:{port}/"),
        row("Data dir:", data_dir),
        row("Log level:", log_level),
        row("MCP endpoint:", f"http://{host}:{port}/mcp"),
        "",
        row("Graph status:", "Initialising in background"),
        "",
        sep,
        "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    _DATA_DIR = Path(cfg["data_dir"])
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_PATH = get_suite_datacontrol_dir() / "logs" / "koredata" / "graph.log"
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _print_banner()
    uvicorn.run(
        "app.server:app",
        host=cfg["host"],
        port=cfg["port"],
        log_level=cfg["log_level"],
        log_config=logutil.make_log_config(_LOG_PATH),
    )
