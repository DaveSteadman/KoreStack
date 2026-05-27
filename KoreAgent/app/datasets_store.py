# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# SQLite spillover store for Scratchpad Datasets.
#
# Stores large per-session datasets outside the KoreChat scratchpad JSON payload while keeping the
# runtime API local to KoreAgent. Each public function opens its own connection so callers do not
# need to manage connection lifetime.
# ====================================================================================================

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from utils.workspace_utils import get_controldata_dir


_DB_PATH: Path | None = None
_wal_initialized: bool = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id    TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    name          TEXT NOT NULL,
    records_json  TEXT NOT NULL,
    meta_json     TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE(session_id, name)
);
"""


def get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        data_dir = get_controldata_dir() / "koreagent"
        data_dir.mkdir(parents=True, exist_ok=True)
        _DB_PATH = data_dir / "datasets.db"
    return _DB_PATH


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    global _wal_initialized
    connection = sqlite3.connect(get_db_path(), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    if not _wal_initialized:
        connection.execute("PRAGMA journal_mode=WAL")
        _wal_initialized = True
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with _conn() as connection:
        connection.executescript(_SCHEMA)


def upsert_dataset(dataset: dict) -> None:
    init_db()
    meta = {
        "schema": dataset.get("schema") or [],
        "source_tool": dataset.get("source_tool") or "",
        "source_args": dataset.get("source_args"),
        "parent_dataset_id": dataset.get("parent_dataset_id") or "",
        "history": dataset.get("history") or [],
        "storage_mode": dataset.get("storage_mode") or "spillover",
        "auto_named": bool(dataset.get("auto_named")),
    }
    with _conn() as connection:
        connection.execute(
            "DELETE FROM datasets WHERE session_id = ? AND name = ? AND dataset_id <> ?",
            (dataset["session_id"], dataset["name"], dataset["dataset_id"]),
        )
        connection.execute(
            """
            INSERT INTO datasets(dataset_id, session_id, name, records_json, meta_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dataset_id) DO UPDATE SET
                session_id=excluded.session_id,
                name=excluded.name,
                records_json=excluded.records_json,
                meta_json=excluded.meta_json,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at
            """,
            (
                dataset["dataset_id"],
                dataset["session_id"],
                dataset["name"],
                json.dumps(dataset.get("records") or [], ensure_ascii=False),
                json.dumps(meta, ensure_ascii=False),
                dataset["created_at"],
                dataset["updated_at"],
            ),
        )


def load_dataset(dataset_id: str) -> dict | None:
    init_db()
    with _conn() as connection:
        row = connection.execute(
            "SELECT dataset_id, session_id, name, records_json, meta_json, created_at, updated_at FROM datasets WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()
    if row is None:
        return None
    meta = json.loads(row["meta_json"] or "{}")
    return {
        "dataset_id": row["dataset_id"],
        "session_id": row["session_id"],
        "name": row["name"],
        "records": json.loads(row["records_json"] or "[]"),
        "schema": meta.get("schema") or [],
        "source_tool": meta.get("source_tool") or "",
        "source_args": meta.get("source_args"),
        "parent_dataset_id": meta.get("parent_dataset_id") or "",
        "history": meta.get("history") or [],
        "storage_mode": meta.get("storage_mode") or "spillover",
        "auto_named": bool(meta.get("auto_named")),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def delete_dataset(dataset_id: str) -> None:
    init_db()
    with _conn() as connection:
        connection.execute("DELETE FROM datasets WHERE dataset_id = ?", (dataset_id,))


def delete_session_datasets(session_id: str) -> None:
    init_db()
    with _conn() as connection:
        connection.execute("DELETE FROM datasets WHERE session_id = ?", (session_id,))
