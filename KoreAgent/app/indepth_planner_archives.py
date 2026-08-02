# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Versioned, portable snapshots of durable InDepthPlanner payloads.  Archives deliberately preserve
# PlanTask references but do not copy transient session datasets or scratchpad contents.
# ====================================================================================================

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.workspace_utils import get_plans_dir


ARCHIVE_FORMAT         = "koreagent-plan"
ARCHIVE_FORMAT_VERSION = 1
ARCHIVE_SUFFIX         = ".plan.json"
_NAME_RE               = re.compile(r"[^a-z0-9]+")


def _exported_at_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _archive_name(path: Path) -> str:
    name = path.name
    return name[:-len(ARCHIVE_SUFFIX)] if name.endswith(ARCHIVE_SUFFIX) else path.stem


def _normalise_name(name: str) -> str:
    normalised = _NAME_RE.sub("-", str(name or "").strip().lower()).strip("-.")
    if not normalised:
        raise RuntimeError("Plan archive name must contain letters or digits.")
    return normalised


def _archive_path_for_name(name: str) -> Path:
    return get_plans_dir() / f"{_normalise_name(name)}{ARCHIVE_SUFFIX}"


def list_plan_archives() -> list[dict[str, Any]]:
    archive_dir = get_plans_dir()
    if not archive_dir.exists():
        return []
    return [
        {
            "name": _archive_name(path),
            "path": str(path),
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        }
        for path in sorted(archive_dir.glob(f"*{ARCHIVE_SUFFIX}"), key=lambda item: item.name.lower())
    ]


def resolve_plan_archive(name: str) -> tuple[Path | None, list[Path]]:
    """Resolve an exact archive name, otherwise a unique case-insensitive substring match."""
    needle = _normalise_name(name)

    archives = [Path(item["path"]) for item in list_plan_archives()]
    exact = [path for path in archives if _normalise_name(_archive_name(path)) == needle]
    if exact:
        return exact[0], []

    matches = [path for path in archives if needle in _normalise_name(_archive_name(path))]
    return (matches[0], []) if len(matches) == 1 else (None, matches)


def export_plan_archive(*, name: str, plan: dict[str, Any], source_conversation_id: object = None) -> dict[str, Any]:
    from indepth_planner_store import _to_persisted_plan

    persisted_plan = _to_persisted_plan(plan) if isinstance(plan, dict) else {}
    if not persisted_plan.get("static"):
        raise RuntimeError("There is no active plan to export.")

    path = _archive_path_for_name(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    archive = {
        "format": ARCHIVE_FORMAT,
        "format_version": ARCHIVE_FORMAT_VERSION,
        "exported_at": _exported_at_timestamp(),
        "plan": {"static": persisted_plan["static"]},
    }
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(archive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return {"name": _archive_name(path), "path": str(path), "archive": archive}


def load_plan_archive(name: str) -> dict[str, Any]:
    path, matches = resolve_plan_archive(name)
    if path is None:
        if matches:
            choices = ", ".join(_archive_name(item) for item in matches)
            raise RuntimeError(f"Plan archive name '{name}' is ambiguous: {choices}.")
        raise RuntimeError(f"No plan archive matching '{name}' found.")
    try:
        archive = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read plan archive '{path.name}': {exc}") from exc
    if not isinstance(archive, dict) or archive.get("format") != ARCHIVE_FORMAT:
        raise RuntimeError(f"'{path.name}' is not a KoreAgent plan archive.")
    try:
        format_version = int(archive.get("format_version") or 0)
    except (TypeError, ValueError):
        format_version = 0
    if format_version != ARCHIVE_FORMAT_VERSION:
        raise RuntimeError(f"'{path.name}' uses unsupported plan archive format version.")
    if not isinstance(archive.get("plan"), dict) or not isinstance(archive["plan"].get("static"), dict):
        raise RuntimeError(f"'{path.name}' does not contain a valid active plan.")
    from indepth_planner_store import _validate_simple_plan
    _validate_simple_plan({"static": archive["plan"]["static"], "dynamic": {"tasks": {}}})
    return {"name": _archive_name(path), "path": str(path), "archive": archive}
