"""Named, persistent KoreData SavedSearch definitions."""
from __future__ import annotations

import json
from pathlib import Path


def _read_saved_searches(path: Path, key: str) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    saved_searches = data.get(key, []) if isinstance(data, dict) else []
    return saved_searches if isinstance(saved_searches, list) else []


def save_saved_searches(path: Path, saved_searches: list[dict]) -> None:
    """Atomically persist the supplied SavedSearch definitions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps({"saved_searches": saved_searches}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_saved_searches(path: Path, legacy_path: Path | None = None) -> list[dict]:
    """Return SavedSearch definitions and migrate the prior store on first use."""
    if path.exists():
        return _read_saved_searches(path, "saved_searches")
    legacy = _read_saved_searches(legacy_path, "output_sets") if legacy_path else []
    if legacy:
        save_saved_searches(path, legacy)
    return legacy


def upsert_saved_search(path: Path, saved_search: dict, legacy_path: Path | None = None) -> dict:
    """Create or replace one SavedSearch by case-insensitive name."""
    name        = str(saved_search["name"])
    current     = load_saved_searches(path, legacy_path)
    replacement = [item for item in current if str(item.get("name", "")).casefold() != name.casefold()]
    replacement.append(saved_search)
    replacement.sort(key=lambda item: str(item.get("name", "")).casefold())
    save_saved_searches(path, replacement)
    return saved_search


def delete_saved_searches(path: Path, names: list[str], legacy_path: Path | None = None) -> list[str]:
    """Delete SavedSearch definitions matching names and return those removed."""
    requested = {str(name).casefold() for name in names}
    current   = load_saved_searches(path, legacy_path)
    removed   = [str(item.get("name", "")) for item in current if str(item.get("name", "")).casefold() in requested]
    if removed:
        save_saved_searches(
            path,
            [item for item in current if str(item.get("name", "")).casefold() not in requested],
        )
    return removed


def find_saved_search(path: Path, name: str, legacy_path: Path | None = None) -> dict | None:
    """Find one SavedSearch by case-insensitive name."""
    needle = str(name).casefold()
    return next(
        (item for item in load_saved_searches(path, legacy_path) if str(item.get("name", "")).casefold() == needle),
        None,
    )
