from __future__ import annotations

# Durable KoreCode UI preferences that must survive a service restart.

import json
import os
from pathlib import Path
from typing import Any

from KoreCommon.suite_paths import get_suite_datacontrol_dir


def _state_path() -> Path:
    explicit = str(os.environ.get("KORECODE_UI_STATE_DIR", "") or "").strip()
    root     = Path(explicit).expanduser().resolve() if explicit else (get_suite_datacontrol_dir() / "korecode").resolve()
    return root / "ui_state.json"


def _load_state() -> dict[str, Any]:
    path = _state_path()
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


def get_active_workspace_root() -> str | None:
    value = _load_state().get("active_workspace_root")
    return str(value).strip() or None if isinstance(value, str) else None


def set_active_workspace_root(workspace_root: Path | str) -> None:
    payload                           = _load_state()
    payload["active_workspace_root"] = str(Path(workspace_root).resolve())
    _write_state(payload)
