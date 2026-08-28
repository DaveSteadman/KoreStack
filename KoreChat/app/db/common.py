from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared KoreChat database helpers used by the split child modules.
#
# This module owns the SQLite connection policy and the small pieces of state that
# every child module relies on:
#   - get_db_path() / _conn() centralise where the database lives and how each
#     short-lived connection is configured.
#   - WAL mode and foreign-key enforcement are applied on every connection rather
#     than cached behind process-global initialisation, so correctness does not
#     depend on import order or thread timing.
#   - JSON repair helpers decode persisted session fields and preserve raw values
#     when older rows contain malformed payloads.
#   - profile / event-type defaults live here so the child modules share one set
#     of rules rather than quietly diverging.
#
# Test support:
#   - reset_runtime_state() clears only module-level cached paths.  It exists so
#     tests can point cfg["data_dir"] at fresh temporary directories.
# MARK: FUNCTIONS
# Function inventory:
# - reset_runtime_state: Implements the reset runtime state operation for this module.
# - get_db_path: Returns db path for this module.
# - _conn: Implements the  conn operation for this module.
# - _now: Implements the  now operation for this module.
# - _row_to_dict: Implements the  row to dict operation for this module.
# - _decode_message_metadata: Expands persisted message metadata for callers.
# - _decode_json_value: Implements the  decode json value operation for this module.
# - _decode_session_state_fields: Implements the  decode session state fields operation for this module.
# - _default_profile: Implements the  default profile operation for this module.
# - _is_protected_subject: Implements the  is protected subject operation for this module.
# - _claimable_event_types_for_consumer: Implements the  claimable event types for consumer operation for this module.
# ====================================================================================================

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Generator

from app.config import cfg


CLAIM_TIMEOUT_SECS = 600

_PROFILE_DEFAULTS: dict[str, str] = {
    "webchat": "admin",
}
_FALLBACK_PROFILE = "external"

_CLAIMABLE_EVENT_TYPES: dict[str, tuple[str, ...]] = {
    "agent":     ("response_needed", "compress_needed", "conversation_closed"),
    "korecomms": ("outbound_ready", "conversation_deleted"),
}

_DB_PATH: Path | None = None
_THREAD_LOCAL = threading.local()


def reset_runtime_state() -> None:
    global _DB_PATH
    connection = getattr(_THREAD_LOCAL, "connection", None)
    if connection is not None:
        connection.close()
    _THREAD_LOCAL.connection      = None
    _THREAD_LOCAL.connection_path = None
    _DB_PATH = None


def get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        data_dir = Path(cfg["data_dir"])
        data_dir.mkdir(parents=True, exist_ok=True)
        _DB_PATH = data_dir / "korechat.db"
    return _DB_PATH


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    db_path = get_db_path()
    connection = getattr(_THREAD_LOCAL, "connection", None)
    if connection is None or getattr(_THREAD_LOCAL, "connection_path", None) != db_path:
        if connection is not None:
            connection.close()
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        # `journal_mode` is database-wide and is set once by init_db(). Reasserting it
        # per request needs an exclusive lock, which can make lightweight /status
        # requests wait behind ordinary conversation writes.
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        _THREAD_LOCAL.connection      = connection
        _THREAD_LOCAL.connection_path = db_path
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def _decode_message_metadata(record: dict) -> None:
    """Keep message state in one JSON column while exposing its established fields."""
    raw_metadata = record.get("metadata") or "{}"
    try:
        metadata = json.loads(raw_metadata) if isinstance(raw_metadata, str) else raw_metadata
    except json.JSONDecodeError:
        metadata = {}
    metadata = metadata if isinstance(metadata, dict) else {}
    record["metadata"] = json.dumps(metadata)
    for key in ("direction", "sender_display", "status", "delivery_eligible", "created_at"):
        if key in metadata:
            record[key] = metadata[key]
    tags = metadata.get("tags")
    record["tags"] = tags if isinstance(tags, list) else []


def _decode_json_value(raw_value: str, default: object, *, label: str) -> tuple[object, str | None]:
    try:
        return json.loads(raw_value or json.dumps(default)), None
    except json.JSONDecodeError as exc:
        detail = f"{label} JSON decode failed: {exc.msg} at line {exc.lineno} column {exc.colno}"
        print(f"[database] Warning: {detail}", flush=True)
        return default, detail


def _decode_session_state_fields(record: dict, *, label: str) -> None:
    raw_scratchpad = str(record.get("scratchpad") or "{}")
    scratchpad, scratchpad_error = _decode_json_value(raw_scratchpad, {}, label=f"{label} scratchpad")
    record["scratchpad"] = scratchpad if isinstance(scratchpad, dict) else {}
    if scratchpad_error:
        record["scratchpad_raw"] = raw_scratchpad
        record["scratchpad_parse_error"] = scratchpad_error

    raw_datasets = str(record.get("datasets") or "{}")
    datasets, datasets_error = _decode_json_value(raw_datasets, {}, label=f"{label} datasets")
    record["datasets"] = datasets if isinstance(datasets, dict) else {}
    if datasets_error:
        record["datasets_raw"] = raw_datasets
        record["datasets_parse_error"] = datasets_error

    raw_tools_active = str(record.get("tools_active") or "[]")
    tools_active, _tools_active_error = _decode_json_value(raw_tools_active, [], label=f"{label} tools_active")
    record["tools_active"] = tools_active if isinstance(tools_active, list) else []

    raw_input_history = str(record.get("input_history") or "[]")
    input_history, _input_history_error = _decode_json_value(raw_input_history, [], label=f"{label} input_history")
    record["input_history"] = input_history if isinstance(input_history, list) else []


def _default_profile(channel_type: str) -> str:
    return _PROFILE_DEFAULTS.get(channel_type, _FALLBACK_PROFILE)


def _is_protected_subject(subject: str | None, external_id: str | None = None) -> int:
    normalized = (subject or "").strip().lower()
    if normalized in ("", "new conversation"):
        return 0
    external = (external_id or "").strip().lower()
    if external.startswith("webchat_") and normalized == f"webchat {external[8:]}":
        return 0
    return 1


def _claimable_event_types_for_consumer(claimed_by: str) -> tuple[str, ...] | None:
    key = (claimed_by or "").strip().lower()
    return _CLAIMABLE_EVENT_TYPES.get(key)
