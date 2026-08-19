# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# SQLite data-access layer for KoreComms.
#
# Schema:
#   interfaces        -- external channel configurations (type, name, enabled, config_json)
#   conversations     -- routing table linking an interface + external thread to a KoreChat ID
#   external_messages -- thin deduplication and reply-anchoring records
#   activity_log      -- operational audit trail
#
# KoreComms does NOT store message content; that lives in KoreChat.
# Each public function creates its own connection so it is safe to call from any thread.
# WAL mode is enabled for better read concurrency.
#
# Related modules:
#   - app/server.py  -- read/write operations via this module
#   - app/poller.py  -- read/write during inbound/outbound polling
# MARK: FUNCTIONS
# Function inventory:
# - get_db_path: Returns db path for this module.
# - _open_connection: Implements the  open connection operation for this module.
# - get_db: Returns db for this module.
# - _ensure_schema: Implements the  ensure schema operation for this module.
# - init_db: Implements the init db operation for this module.
# - _now: Implements the  now operation for this module.
# - _row_to_dict: Implements the  row to dict operation for this module.
# - config_get: Implements the config get operation for this module.
# - config_set: Implements the config set operation for this module.
# - interface_list: Implements the interface list operation for this module.
# - interface_get: Implements the interface get operation for this module.
# - interface_get_manual: Implements the interface get manual operation for this module.
# - interface_create: Implements the interface create operation for this module.
# - interface_update: Implements the interface update operation for this module.
# - interface_delete: Implements the interface delete operation for this module.
# - conversation_list: Implements the conversation list operation for this module.
# - conversation_create: Implements the conversation create operation for this module.
# - conversation_get: Implements the conversation get operation for this module.
# - conversation_set_kc_id: Implements the conversation set kc id operation for this module.
# - conversation_set_name: Implements the conversation set name operation for this module.
# - conversation_get_by_external_thread: Implements the conversation get by external thread operation for this module.
# - conversation_get_by_name: Implements the conversation get by name operation for this module.
# - conversation_get_by_kc_id: Implements the conversation get by kc id operation for this module.
# - conversation_list_with_kc_id: Implements the conversation list with kc id operation for this module.
# - conversation_delete: Implements the conversation delete operation for this module.
# - external_message_exists: Implements the external message exists operation for this module.
# - external_message_create: Implements the external message create operation for this module.
# - conversation_set_delivery: Implements the conversation set delivery operation for this module.
# - conversation_bind_delivery: Implements the conversation bind delivery operation for this module.
# - distribution_list_create: Implements the distribution list create operation for this module.
# - distribution_list_list: Implements the distribution list list operation for this module.
# - distribution_list_get: Implements the distribution list get operation for this module.
# - distribution_list_delete: Implements the distribution list delete operation for this module.
# - distribution_list_update: Implements the distribution list update operation for this module.
# - distribution_list_members: Implements the distribution list members operation for this module.
# - distribution_list_member_add: Implements the distribution list member add operation for this module.
# - distribution_list_member_delete: Implements the distribution list member delete operation for this module.
# - distribution_list_member_update: Implements the distribution list member update operation for this module.
# - external_message_get_last_inbound: Implements the external message get last inbound operation for this module.
# - log_activity: Implements the log activity operation for this module.
# - activity_list: Implements the activity list operation for this module.
# ====================================================================================================
from __future__ import annotations

import json
import queue
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from app.config import cfg

_DB_PATH: Path | None = None
_SCHEMA_READY = False
_SCHEMA_LOCK  = threading.Lock()
_CONNECTION_POOL: queue.LifoQueue[sqlite3.Connection] = queue.LifoQueue(maxsize=8)


def get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        data_dir = Path(cfg["data_dir"])
        data_dir.mkdir(parents=True, exist_ok=True)
        _DB_PATH = data_dir / "korecomms.db"
    return _DB_PATH


def _open_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(), check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    init_db()
    try:
        conn = _CONNECTION_POOL.get_nowait()
    except queue.Empty:
        conn = _open_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            _CONNECTION_POOL.put_nowait(conn)
        except queue.Full:
            conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interfaces (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,
    name        TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    interface_id       INTEGER NOT NULL REFERENCES interfaces(id) ON DELETE CASCADE,
    chat_name  TEXT,
    kc_chat_id INTEGER,
    external_thread_id TEXT,
    korechat_id TEXT,
    delivery_recipient TEXT,
    delivery_list_id INTEGER REFERENCES distribution_lists(id) ON DELETE SET NULL,
    delivery_subject TEXT,
    delivery_enabled INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS distribution_lists (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    interface_id INTEGER NOT NULL REFERENCES interfaces(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    UNIQUE(interface_id, name)
);

CREATE TABLE IF NOT EXISTS distribution_list_members (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id     INTEGER NOT NULL REFERENCES distribution_lists(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE(list_id, email)
);

CREATE TABLE IF NOT EXISTS external_messages (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id      INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    external_message_id  TEXT NOT NULL,
    direction            TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
    sender_display       TEXT NOT NULL DEFAULT '',
    received_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    action    TEXT NOT NULL,
    detail    TEXT,
    logged_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_convs_iface        ON conversations(interface_id);
CREATE INDEX IF NOT EXISTS idx_convs_kc_id        ON conversations(kc_chat_id);
CREATE INDEX IF NOT EXISTS idx_convs_thread       ON conversations(external_thread_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_convs_thread_unique ON conversations(external_thread_id)
    WHERE external_thread_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_convs_name  ON conversations(chat_name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ext_msg_id  ON external_messages(external_message_id);
CREATE INDEX IF NOT EXISTS idx_ext_msg_conv       ON external_messages(conversation_id, direction);
CREATE INDEX IF NOT EXISTS idx_dist_lists_iface   ON distribution_lists(interface_id);
CREATE INDEX IF NOT EXISTS idx_dist_members_list  ON distribution_list_members(list_id);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conv_cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
    if conv_cols and "chat_name" not in conv_cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN chat_name TEXT")
    if conv_cols and "kc_chat_id" not in conv_cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN kc_chat_id INTEGER")
    if conv_cols and "subject" in conv_cols and "korechat_id" not in conv_cols:
        conn.execute("ALTER TABLE conversations RENAME COLUMN subject TO korechat_id")
        conv_cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
    if conv_cols and "korechat_id" not in conv_cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN korechat_id TEXT")
    if conv_cols and "delivery_recipient" not in conv_cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN delivery_recipient TEXT")
    if conv_cols and "delivery_list_id" not in conv_cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN delivery_list_id INTEGER")
    if conv_cols and "delivery_subject" not in conv_cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN delivery_subject TEXT")
    if conv_cols and "delivery_enabled" not in conv_cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN delivery_enabled INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "UPDATE conversations "
        "SET chat_name = COALESCE(NULLIF(external_thread_id, ''), 'kccomms:' || id) "
        "WHERE chat_name IS NULL OR chat_name = ''"
    )
    row = conn.execute("SELECT id FROM interfaces WHERE type='manual' LIMIT 1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO interfaces (type, name, config_json, enabled, created_at) "
            "VALUES ('manual', 'Manual', '{}', 1, ?)",
            (_now(),),
        )


def init_db() -> None:
    """Create tables, run migrations, and seed the permanent Manual interface."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = _open_connection()
        try:
            # WAL mode is durable database configuration, not per-request work.
            conn.execute("PRAGMA journal_mode=WAL")
            _ensure_schema(conn)
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            if _SCHEMA_READY:
                _CONNECTION_POOL.put(conn)
            else:
                conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Config table
# ---------------------------------------------------------------------------

def config_get(key: str, default: str | None = None) -> str | None:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def config_set(key: str, value: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO config(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------

def interface_list() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM interfaces ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def interface_get(iface_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM interfaces WHERE id=?", (iface_id,)).fetchone()
    return _row_to_dict(row)


def interface_get_manual() -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM interfaces WHERE type='manual' LIMIT 1").fetchone()
    return dict(row)


def interface_create(type_: str, name: str, config_json: dict) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO interfaces (type, name, config_json, enabled, created_at) "
            "VALUES (?,?,?,1,?)",
            (type_, name, json.dumps(config_json), _now()),
        )
    return cur.lastrowid  # type: ignore[return-value]


def interface_update(iface_id: int, name: str, config_json: dict, enabled: bool) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE interfaces SET name=?, config_json=?, enabled=? WHERE id=?",
            (name, json.dumps(config_json), int(enabled), iface_id),
        )


def interface_delete(iface_id: int) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM interfaces WHERE id=? AND type != 'manual'", (iface_id,))


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def conversation_list(limit: int = 100, offset: int = 0) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT c.*, i.name AS interface_name, i.type AS interface_type "
            "FROM conversations c "
            "JOIN interfaces i ON i.id = c.interface_id "
            "ORDER BY c.id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def conversation_create(
    interface_id:       int,
    kc_chat_id:         int | None = None,
    external_thread_id: str | None = None,
    korechat_id:        str | None = None,
    chat_name:          str | None = None,
    delivery_enabled:   bool = False,
) -> int:
    with get_db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO conversations "
                "(interface_id, chat_name, kc_chat_id, external_thread_id, korechat_id, delivery_enabled, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (interface_id, chat_name, kc_chat_id, external_thread_id, korechat_id, int(delivery_enabled), _now()),
            )
            row_id = int(cur.lastrowid)
            if not chat_name:
                chat_name = f"kccomms:{row_id}"
                conn.execute(
                    "UPDATE conversations SET chat_name=? WHERE id=?",
                    (chat_name, row_id),
                )
            return row_id
        except sqlite3.IntegrityError:
            if external_thread_id:
                row = conn.execute(
                    "SELECT id FROM conversations WHERE external_thread_id=? LIMIT 1",
                    (external_thread_id,),
                ).fetchone()
                if row is not None:
                    return int(row["id"])
            raise


def conversation_get(conv_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT c.*, i.name AS interface_name, i.type AS interface_type "
            "FROM conversations c JOIN interfaces i ON i.id = c.interface_id "
            "WHERE c.id=?",
            (conv_id,),
        ).fetchone()
    return _row_to_dict(row)


def conversation_set_kc_id(conv_id: int, kc_chat_id: int | None) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE conversations SET kc_chat_id=? WHERE id=?",
            (kc_chat_id, conv_id),
        )


def conversation_set_name(conv_id: int, chat_name: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE conversations SET chat_name=? WHERE id=?",
            (chat_name, conv_id),
        )


def conversation_get_by_external_thread(external_thread_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT c.*, i.name AS interface_name, i.type AS interface_type "
            "FROM conversations c JOIN interfaces i ON i.id = c.interface_id "
            "WHERE c.external_thread_id=? LIMIT 1",
            (external_thread_id,),
        ).fetchone()
    return _row_to_dict(row)


def conversation_get_by_name(chat_name: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT c.*, i.name AS interface_name, i.type AS interface_type "
            "FROM conversations c JOIN interfaces i ON i.id = c.interface_id "
            "WHERE c.chat_name=? LIMIT 1",
            (chat_name,),
        ).fetchone()
    return _row_to_dict(row)


def conversation_get_by_kc_id(kc_chat_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT c.*, i.name AS interface_name, i.type AS interface_type "
            "FROM conversations c JOIN interfaces i ON i.id = c.interface_id "
            "WHERE c.kc_chat_id=? LIMIT 1",
            (kc_chat_id,),
        ).fetchone()
    return _row_to_dict(row)


def conversation_list_with_kc_id() -> list[dict]:
    """Return all routing conversations that have a linked KC conversation ID."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE kc_chat_id IS NOT NULL"
        ).fetchall()
    return [dict(r) for r in rows]


def conversation_delete(conv_id: int) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))


# ---------------------------------------------------------------------------
# External messages — deduplication and reply anchoring
# ---------------------------------------------------------------------------

def external_message_exists(external_message_id: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM external_messages WHERE external_message_id=? LIMIT 1",
            (external_message_id,),
        ).fetchone()
    return row is not None


def external_message_create(
    conversation_id:     int,
    external_message_id: str,
    direction:           str,
    sender_display:      str = "",
) -> bool:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO external_messages "
            "(conversation_id, external_message_id, direction, sender_display, received_at) "
            "VALUES (?,?,?,?,?)",
            (conversation_id, external_message_id, direction, sender_display, _now()),
        )
    return cur.rowcount > 0


def conversation_set_delivery(
    conv_id: int,
    recipient: str,
    list_id: int | None,
    subject: str,
    enabled: bool,
) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE conversations SET delivery_recipient=?, delivery_list_id=?, delivery_subject=?, delivery_enabled=? WHERE id=?",
            (recipient, list_id, subject, int(enabled), conv_id),
        )


def conversation_bind_delivery(
    interface_id:     int,
    chat_name:        str,
    korechat_id:      str,
    recipient:        str,
    list_id:          int | None,
    subject:          str,
    enabled:          bool,
    activity_detail:  str,
) -> tuple[int, bool]:
    """Create or update a delivery-bound conversation in one database transaction.

    Returns the local conversation ID and whether it was newly created.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, interface_id FROM conversations WHERE chat_name=? LIMIT 1",
            (chat_name,),
        ).fetchone()
        created = row is None
        if created:
            cur = conn.execute(
                "INSERT INTO conversations "
                "(interface_id, chat_name, korechat_id, created_at) VALUES (?,?,?,?)",
                (interface_id, chat_name, korechat_id, _now()),
            )
            conv_id = int(cur.lastrowid)
        else:
            assert row is not None
            conv_id = int(row["id"])

        conn.execute(
            "UPDATE conversations SET interface_id=?, korechat_id=?, delivery_recipient=?, delivery_list_id=?, "
            "delivery_subject=?, delivery_enabled=? WHERE id=?",
            (interface_id, korechat_id, recipient, list_id, subject, int(enabled), conv_id),
        )
        conn.execute(
            "INSERT INTO activity_log (action, detail, logged_at) VALUES (?,?,?)",
            ("delivery_rebound" if not created else "delivery_bound", activity_detail, _now()),
        )
    return conv_id, created


# ---------------------------------------------------------------------------
# Distribution lists
# ---------------------------------------------------------------------------

def distribution_list_create(interface_id: int, name: str, description: str = "") -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO distribution_lists (interface_id, name, description, created_at) VALUES (?,?,?,?)",
            (interface_id, name.strip(), description.strip(), _now()),
        )
    return int(cur.lastrowid)


def distribution_list_list(interface_id: int | None = None) -> list[dict]:
    query  = (
        "SELECT dl.*, i.name AS interface_name, COUNT(dlm.id) AS member_count "
        "FROM distribution_lists dl JOIN interfaces i ON i.id=dl.interface_id "
        "LEFT JOIN distribution_list_members dlm ON dlm.list_id=dl.id "
    )
    params: tuple = ()
    if interface_id is not None:
        query += "WHERE dl.interface_id=? "
        params = (interface_id,)
    query += "GROUP BY dl.id ORDER BY i.name COLLATE NOCASE, dl.name COLLATE NOCASE"
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def distribution_list_get(list_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT dl.*, i.name AS interface_name FROM distribution_lists dl "
            "JOIN interfaces i ON i.id=dl.interface_id WHERE dl.id=?",
            (list_id,),
        ).fetchone()
    return _row_to_dict(row)


def distribution_list_delete(list_id: int) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM distribution_lists WHERE id=?", (list_id,))


def distribution_list_update(list_id: int, name: str, description: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE distribution_lists SET name=?, description=? WHERE id=?",
            (name.strip(), description.strip(), list_id),
        )


def distribution_list_members(list_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM distribution_list_members WHERE list_id=? ORDER BY display_name COLLATE NOCASE, email COLLATE NOCASE",
            (list_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def distribution_list_member_add(list_id: int, email: str, display_name: str = "") -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO distribution_list_members (list_id, email, display_name, created_at) VALUES (?,?,?,?)",
            (list_id, email.strip(), display_name.strip(), _now()),
        )
    return int(cur.lastrowid)


def distribution_list_member_delete(list_id: int, member_id: int) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM distribution_list_members WHERE id=? AND list_id=?", (member_id, list_id))


def distribution_list_member_update(list_id: int, member_id: int, email: str, display_name: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE distribution_list_members SET email=?, display_name=? WHERE id=? AND list_id=?",
            (email.strip(), display_name.strip(), member_id, list_id),
        )


def external_message_get_last_inbound(conversation_id: int) -> dict | None:
    """Return the most recent inbound external message for reply anchoring."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM external_messages "
            "WHERE conversation_id=? AND direction='inbound' "
            "ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------

def log_activity(action: str, detail: str | None = None) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO activity_log (action, detail, logged_at) VALUES (?,?,?)",
            (action, detail, _now()),
        )


def activity_list(limit: int = 200) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
