# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Entry point for KoreDevice/KoreDeviceDriver.
# Bootstraps the package application or utility from the command line.
# ====================================================================================================

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "CommonCode"))

import logutil
import uvicorn
from app.config import cfg
from config import get_suite_datacontrol_dir

_W = 80


def _print_banner() -> None:
    now = datetime.now().strftime("%H:%M:%S")
    sep = "=" * _W

    def row(label: str, value: str) -> str:
        return f"  {label:<24} {value}"

    lines = [
        "",
        sep,
        f"  KOREDEVICEDRIVER  [{now}]",
        sep,
        "",
        row("Service:",               f"http://localhost:{cfg['port']}/"),
        row("Data dir:",              cfg["data_dir"]),
        row("Default protocol:",      cfg["default_protocol"]),
        row("Default poll interval:", str(cfg["default_poll_interval"])),
        row("Log level:",             cfg["log_level"].upper()),
        "",
        sep,
        "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    data_dir = Path(cfg["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = get_suite_datacontrol_dir() / "logs" / "koredevice" / "driver.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _print_banner()
    uvicorn.run(
        "app.server:app",
        host       = cfg["host"],
        port       = cfg["port"],
        log_level  = cfg["log_level"],
        log_config = logutil.make_log_config(log_path),
        reload     = False,
    )
