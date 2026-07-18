from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Durable local work items for KoreCode Gen2. A work item is the user-visible container for scope,
# agent runs, planned work, and the eventual engineering outcome.
# ====================================================================================================

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from KoreCommon.suite_paths import get_suite_datacontrol_dir


WORK_ITEM_STATUSES = {
    "scoping",
    "investigating",
    "plan_ready",
    "awaiting_approval",
    "editing",
    "validating",
    "completed",
    "failed",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _items_root() -> Path:
    explicit = str(os.environ.get("KORECODE_WORK_ITEMS_DIR", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (get_suite_datacontrol_dir() / "korecode" / "work_items").resolve()


def _ensure_items_root() -> Path:
    root = _items_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _item_path(work_item_id: str) -> Path:
    return _ensure_items_root() / f"{work_item_id}.json"


def _write(item: dict[str, Any]) -> dict[str, Any]:
    _item_path(str(item["work_item_id"])).write_text(
        json.dumps(item, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return item


def _load(work_item_id: str) -> dict[str, Any]:
    path = _item_path(work_item_id)
    if not path.exists():
        raise FileNotFoundError(work_item_id)
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid work item: {work_item_id}")
    return payload


def create_work_item(
    *,
    title: str,
    workspace_root: Path,
    description: str = "",
    scope: list[str] | None = None,
    constraints: list[str] | None = None,
) -> dict[str, Any]:
    clean_title = str(title or "").strip()
    if not clean_title:
        raise ValueError("title cannot be empty")

    now  = _utc_now()
    item = {
        "work_item_id":   uuid.uuid4().hex,
        "title":          clean_title,
        "description":    str(description or "").strip(),
        "status":         "scoping",
        "workspace_root": str(workspace_root.resolve()),
        "scope":          [str(value).strip() for value in (scope or []) if str(value).strip()],
        "constraints":    [str(value).strip() for value in (constraints or []) if str(value).strip()],
        "plan":           [],
        "evidence":       [],
        "run_ids":        [],
        "outcome":        None,
        "created_at":     now,
        "updated_at":     now,
    }
    return _write(item)


def get_work_item(work_item_id: str) -> dict[str, Any]:
    return _load(work_item_id)


def list_work_items(*, workspace_root: Path, limit: int = 100) -> list[dict[str, Any]]:
    root  = _ensure_items_root()
    items = []
    for path in sorted(root.glob("*.json"), key=lambda value: value.stat().st_mtime, reverse=True):
        with path.open(encoding="utf-8") as handle:
            item = json.load(handle)
        if not isinstance(item, dict) or item.get("workspace_root") != str(workspace_root.resolve()):
            continue
        items.append(item)
        if len(items) >= max(1, min(int(limit), 500)):
            break
    return items


def update_work_item(work_item_id: str, **changes: Any) -> dict[str, Any]:
    item = _load(work_item_id)
    allowed = {"title", "description", "status", "scope", "constraints", "plan", "evidence", "outcome"}
    for key, value in changes.items():
        if key not in allowed or value is None:
            continue
        if key == "status":
            value = str(value).strip()
            if value not in WORK_ITEM_STATUSES:
                raise ValueError(f"Unknown work item status: {value}")
        if key in {"scope", "constraints", "plan", "evidence"} and not isinstance(value, list):
            raise ValueError(f"{key} must be a list")
        item[key] = value
    item["updated_at"] = _utc_now()
    return _write(item)


def attach_run(work_item_id: str, run_id: str) -> dict[str, Any]:
    item        = _load(work_item_id)
    clean_run_id = str(run_id or "").strip()
    if clean_run_id and clean_run_id not in item["run_ids"]:
        item["run_ids"].append(clean_run_id)
        item["updated_at"] = _utc_now()
        _write(item)
    return item
