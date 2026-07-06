# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Database registry for KoreRAG — multi-database support.
#
# Scans {data_dir}/databases/ for actual database files at startup.
# Each database may have a companion .json descriptor file controlling
# display_name, managed_by, ingestor, navigation, etc.
# Script-only folders remain available to the processing UI, but they are not registered as
# databases until a real .db file exists.
#
# All databases live under {data_dir}/databases/{name}/ (subfolder layout).
# Legacy flat .db files directly in databases/ are also discovered as a fallback.
#
# Public interface:
#   get_db_path(db: str) -> Path          -- resolve database id to its .db file Path
#   list_databases() -> list[dict]        -- descriptor dicts for all registered databases
#   list_database_ids() -> list[str]      -- ids only
#   reload() -> None                      -- re-scan the databases/ directory
#
# Descriptor JSON schema (all fields optional):
#   {
#     "display_name": "Hansard Debates",
#     "description":  "UK Parliament Hansard debates 2015–present",
#     "source_url":   "https://hansard.parliament.uk",
#     "licence":      "Open Parliament Licence",
#     "managed_by":   "ingestor",          // "user" | "ingestor"
#     "ingestor":     "hansard",           // ingestor module name
#     "schedule":     "manual",           // "manual" | "daily" | "weekly" | "monthly"
#     "chunk_types":  ["speech", "question", "answer"],
#     "navigation":   {"type": "hansard"},
#     "sync":         {"last_run": null, "status": "idle"}
#   }
#
# Related modules:
#   - app/database.py  -- passes db id to db_connection()
#   - app/server.py    -- /databases endpoints; init_db() for each registered db
# ====================================================================================================
import json
from pathlib import Path
from typing import Optional

from app.config import cfg

_DATA_DIR = Path(cfg["data_dir"])
_DBS_DIR  = _DATA_DIR / "databases"

# Internal registry: {id: descriptor_dict_with_db_path_key}
_registry: dict[str, dict] = {}


def _load_descriptor(db_id: str, db_path: Path) -> dict:
    """Load .json descriptor alongside .db file, or return defaults if absent."""
    json_path = db_path.with_suffix(".json")
    d: dict = {}
    if json_path.exists():
        try:
            with open(json_path, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            pass
    return {
        "id":           db_id,
        "display_name": d.get("display_name") or db_id.replace("_", " ").title(),
        "description":  d.get("description"),
        "source_url":   d.get("source_url"),
        "licence":      d.get("licence"),
        "managed_by":   d.get("managed_by", "user"),
        "ingestor":     d.get("ingestor"),
        "schedule":     d.get("schedule"),
        "chunk_types":  d.get("chunk_types", []),
        "navigation":   d.get("navigation"),
        "sync":         d.get("sync"),
        "db_path":      db_path,
    }


def reload() -> None:
    """Re-scan data_dir for databases.  Safe to call at any time."""
    _registry.clear()

    # All databases live in databases/ subdirectory.
    if _DBS_DIR.exists():
        # Primary: per-database subfolders — databases/{name}/{name}.db|.json
        for subdir in sorted(p for p in _DBS_DIR.iterdir() if p.is_dir()):
            name = subdir.name
            db_file = subdir / f"{name}.db"
            if db_file.exists() and name not in _registry:
                _registry[name] = _load_descriptor(name, db_file)

        # Legacy fallback: flat .db files directly in databases/
        for db_file in sorted(_DBS_DIR.glob("*.db")):
            name = db_file.stem
            if name not in _registry:
                _registry[name] = _load_descriptor(name, db_file)

        # Orphan descriptors without a matching .db are intentionally skipped.
        # They may describe a processing script, but they are not databases yet.
        for json_file in sorted(_DBS_DIR.glob("*.json")):
            name = json_file.stem
            expected_db = json_file.with_suffix(".db")
            if expected_db.exists() and name not in _registry:
                _registry[name] = _load_descriptor(name, expected_db)


reload()  # Auto-initialize at import time.


def _canonical_subdir_db_path(db: str) -> Path:
    return _DBS_DIR / db / f"{db}.db"


def _canonical_flat_db_path(db: str) -> Path:
    return _DBS_DIR / f"{db}.db"


def _resolve_unregistered_db_path(db: str) -> Optional[Path]:
    subdir_db_path = _canonical_subdir_db_path(db)
    if subdir_db_path.exists() or subdir_db_path.with_suffix(".json").exists():
        return subdir_db_path

    flat_db_path = _canonical_flat_db_path(db)
    if flat_db_path.exists() or flat_db_path.with_suffix(".json").exists():
        return flat_db_path

    return None


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def get_db_path(db: str = "default") -> Path:
    """Resolve a database id to its .db file Path.  Raises KeyError if unknown."""
    entry = _registry.get(db)
    if entry is None:
        path = _resolve_unregistered_db_path(db)
        if path is None:
            raise KeyError(f"Unknown database: {db!r}")
    else:
        path = entry["db_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def list_databases() -> list[dict]:
    """Return all registered databases as descriptor dicts (db_path key excluded)."""
    result = []
    for entry in _registry.values():
        result.append({k: v for k, v in entry.items() if k != "db_path"})
    return result


def list_database_ids() -> list[str]:
    """Return all registered database ids."""
    return list(_registry.keys())


def get_descriptor(db: str) -> Optional[dict]:
    """Return the descriptor dict for a single database, or None if unknown."""
    entry = _registry.get(db)
    if entry is None:
        return None
    return {k: v for k, v in entry.items() if k != "db_path"}
