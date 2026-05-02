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
    sep = "=" * _W

    def row(label: str, value: str) -> str:
        return f"  {label:<24} {value}"

    lines = [
        "",
        sep,
        f"  KOREDATAGATEWAY  [{now}]",
        sep,
        "",
        row("Gateway:", f"http://localhost:{cfg['port']}/"),
        row("KoreFeed:", cfg["korefeed_url"]),
        row("KoreLibrary:", cfg["korelibrary_url"]),
        row("KoreRAG:", cfg["korerag_url"]),
        row("KoreReference:", cfg["korereference_url"]),
        row("Log level:", cfg["log_level"].upper()),
        "",
        sep,
        "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    _LOG_PATH = get_suite_datacontrol_dir() / "logs" / "koredata" / "gateway.log"
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _print_banner()
    uvicorn.run(
        "app.server:app",
        host=cfg["host"],
        port=cfg["port"],
        log_level=cfg["log_level"],
        log_config=logutil.make_log_config(_LOG_PATH),
    )
