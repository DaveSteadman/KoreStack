from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock

from KoreCommon.suite_paths import get_suite_datacontrol_dir

_MAX_ENTRIES     = 500
_TRIM_TO_LINES   = 500
_TRIM_THRESHOLD  = 650
_LOCK            = Lock()
_NEXT_ID         = 1


def _activity_log_path() -> Path:
    path = get_suite_datacontrol_dir() / "koreliveweb" / "activity_log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _scan_last_id(path: Path) -> int:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0

    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except Exception:
            continue
        try:
            return int(payload.get("id") or 0)
        except Exception:
            continue
    return 0


def _next_id() -> int:
    global _NEXT_ID

    if _NEXT_ID == 1:
        _NEXT_ID = max(1, _scan_last_id(_activity_log_path()) + 1)

    value    = _NEXT_ID
    _NEXT_ID += 1
    return value


def _trim_file(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return

    if len(lines) <= _TRIM_THRESHOLD:
        return

    kept = lines[-_TRIM_TO_LINES:]
    try:
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    except OSError:
        return


def append_activity(
    *,
    kind     : str,
    target   : str,
    status   : str,
    message  : str = "",
    tool_name: str = "",
    final_url: str = "",
) -> dict:
    now   = datetime.now()
    entry = {
        "id":        0,
        "ts":        now.isoformat(timespec="seconds"),
        "ts_label":  now.strftime("%H:%M:%S"),
        "kind":      str(kind or "").strip() or "event",
        "tool_name": str(tool_name or "").strip(),
        "target":    str(target or "").strip(),
        "status":    str(status or "").strip() or "ok",
        "message":   str(message or "").strip(),
        "final_url": str(final_url or "").strip(),
    }

    path = _activity_log_path()
    with _LOCK:
        entry["id"] = _next_id()
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
        except OSError:
            pass
        _trim_file(path)

    return dict(entry)


def list_activity(*, limit: int = 200) -> list[dict]:
    capped = max(1, min(int(limit), _MAX_ENTRIES))
    path   = _activity_log_path()

    with _LOCK:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []

    entries: list[dict] = []
    for line in reversed(lines):
        if len(entries) >= capped:
            break
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            entries.append(payload)

    return entries
