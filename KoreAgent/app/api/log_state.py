import queue
import threading
from datetime import datetime
from pathlib import Path


_log_subscribers: list[queue.Queue] = []
_log_subscribers_lock: threading.Lock = threading.Lock()


def get_log_subscribers() -> list[queue.Queue]:
    return _log_subscribers


def get_log_subscribers_lock() -> threading.Lock:
    return _log_subscribers_lock


def push_log_line(line: str, *, latest_log_path_getter) -> None:
    item = {
        "type": "log",
        "text": line,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "path": latest_log_path_getter(),
    }
    with _log_subscribers_lock:
        for sub in list(_log_subscribers):
            try:
                sub.put_nowait(item)
            except queue.Full:
                pass


def get_latest_log_file(log_dir: Path) -> Path | None:
    if not log_dir.exists():
        return None
    day_dirs = sorted(log_dir.iterdir(), reverse=True)
    for day_dir in day_dirs:
        if not day_dir.is_dir():
            continue
        files = sorted(day_dir.glob("*.txt"), reverse=True)
        if files:
            return files[0]
    return None


def get_log_backfill(
    *,
    log_dir: Path,
    tail_lines: int,
    set_latest_log_path,
) -> list[dict]:
    latest = get_latest_log_file(log_dir)
    if latest is None:
        return []
    try:
        lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = lines[-tail_lines:]
        set_latest_log_path(latest)
        return [{"type": "log", "text": line, "ts": "", "path": str(latest)} for line in tail]
    except Exception:
        return []
