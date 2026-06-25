# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Entry point for KoreData/KoreScrape.
# Bootstraps the package application or utility from the command line.
# ====================================================================================================

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "CommonCode"))

import logutil
import uvicorn
from app.config import cfg
from app.server import get_status
from config import get_suite_datacontrol_dir

_W = 80


def _print_banner() -> None:
    now      = datetime.now().strftime("%H:%M:%S")
    stats    = get_status()
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
        row("Captures:",    str(stats["captures"])),
        row("Running jobs:", str(stats["running_jobs"])),
        row("Saved pages:", str(stats["pages"])),
        row("Saved assets:", str(stats["assets"])),
        "",
        sep,
        "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    data_dir  = Path(cfg["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path  = get_suite_datacontrol_dir() / "logs" / "koredata" / "scrape.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _print_banner()
    uvicorn.run(
        "app.server:app",
        host       = cfg["host"],
        port       = cfg["port"],
        log_level  = cfg["log_level"],
        log_config = logutil.make_log_config(log_path),
    )
