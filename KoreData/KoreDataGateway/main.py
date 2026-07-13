import os
import signal
import subprocess
import sys
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "CommonCode"))

import logutil
import uvicorn
from app.config import cfg

_W = 80


def _listening_pids_on_port(port: int) -> list[int]:
    try:
        output = subprocess.check_output(["netstat", "-ano"], text=True, encoding="utf-8", errors="ignore")
    except Exception:
        return []
    pids: list[int] = []
    needle = f":{port}"
    for line in output.splitlines():
        text = line.strip()
        if "LISTENING" not in text or needle not in text:
            continue
        parts = text.split()
        if len(parts) < 5:
            continue
        local_addr = parts[1]
        state      = parts[3]
        pid_text   = parts[4]
        if not local_addr.endswith(needle) or state != "LISTENING":
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


def _terminate_pid(pid: int, label: str) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    print(f"  [stale] Clearing {label} listener  (pid {pid})")
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:
        return


def _clear_stale_gateway_listener() -> None:
    port = int(cfg["port"])
    if port <= 0:
        return
    for pid in _listening_pids_on_port(port):
        _terminate_pid(pid, "KoreDataGateway")


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
        row("Gateway:",      f"http://localhost:{cfg['port']}/"),
        row("KoreFeed:",     cfg["korefeed_url"]),
        row("KoreLibrary:",  cfg["korelibrary_url"]),
        row("KoreRAG:",      cfg["korerag_url"]),
        row("KoreReference:", cfg["korereference_url"]),
        row("Log level:",    cfg["log_level"].upper()),
        "",
        sep,
        "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    logutil.configure_service_logging("koredatagateway", cfg["log_level"])
    try:
        logging.getLogger("koredatagateway.service").info("starting host=%s port=%s", cfg["host"], cfg["port"])
        _print_banner()
        _clear_stale_gateway_listener()
        uvicorn.run(
            "app.server:app",
            host       = cfg["host"],
            port       = cfg["port"],
            access_log = False,
            log_level  = cfg["log_level"],
            log_config = logutil.make_log_config("koredatagateway", cfg["log_level"]),
        )
    except Exception:
        logging.getLogger("koredatagateway.service").exception("startup failed")
        raise
