# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# SQLite data-access layer for KoreConversation.
#
# Each public function creates its own connection so it is safe to call from any thread.
# WAL mode is enabled so readers and writers can overlap without blocking.
#
# Schema summary:
#   conversations  - canonical conversation record (summary, scratchpad, profile, status)
#   messages       - append-only exchange log (inbound/outbound, summarised flag)
#   events         - coordination queue between MiniAgentFramework and KoreComms
#                    (atomic claim via BEGIN IMMEDIATE prevents double-pickup)
#
# Claim timeout reaper:
#   release_stale_claims() resets events claimed longer ago than CLAIM_TIMEOUT_SECS.
#   Called from the background reaper thread started in api.py lifespan.
# ====================================================================================================

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Generator

from app.config import cfg

# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================

CLAIM_TIMEOUT_SECS = 600  # 10 minutes

_PROFILE_DEFAULTS: dict[str, str] = {
    "webchat": "admin",
}
_FALLBACK_PROFILE = "external"

_CLAIMABLE_EVENT_TYPES: dict[str, tuple[str, ...]] = {
    "agent": ("response_needed", "compress_needed", "conversation_closed"),
    "korecomms": ("outbound_ready", "conversation_deleted"),
}

_DB_PATH: Path | None = None
_wal_initialized:  bool   = False  # set True after first init_db so _conn skips the WAL pragma


# ====================================================================================================
# MARK: CONNECTION
# ====================================================================================================

def get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        data_dir = Path(cfg["data_dir"])
        data_dir.mkdir(parents=True, exist_ok=True)
        _DB_PATH = data_dir / "koreconversation.db"
    return _DB_PATH


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    c = sqlite3.connect(get_db_path(), check_same_thread=False)
    c.row_factory = sqlite3.Row
    if not _wal_initialized:
        c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


# ====================================================================================================
# MARK: SCHEMA
# ====================================================================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_type        TEXT    NOT NULL DEFAULT 'webchat',
    profile             TEXT    NOT NULL DEFAULT 'admin'
                                CHECK(profile IN ('admin','external','readonly')),
    status              TEXT    NOT NULL DEFAULT 'active'
                                CHECK(status IN ('active','waiting_agent','agent_processing','archived','deleted')),
    subject             TEXT,
    external_id         TEXT,
    thread_summary      TEXT    NOT NULL DEFAULT '',
    scratchpad          TEXT    NOT NULL DEFAULT '{}',
    input_history       TEXT    NOT NULL DEFAULT '[]',
    background_context  TEXT    NOT NULL DEFAULT '',
    token_estimate      INTEGER NOT NULL DEFAULT 0,
    turn_count          INTEGER NOT NULL DEFAULT 0,
    last_activity_at    TEXT    NOT NULL,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    direction        TEXT    NOT NULL CHECK(direction IN ('inbound','outbound')),
    content          TEXT    NOT NULL,
    sender_display   TEXT    NOT NULL DEFAULT '',
    status           TEXT    NOT NULL DEFAULT 'received'
                             CHECK(status IN ('received','draft','sent','failed')),
    summarised       INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    event_type       TEXT    NOT NULL
                             CHECK(event_type IN (
                                 'response_needed','outbound_ready','compress_needed',
                                 'conversation_closed','conversation_deleted'
                             )),
    status           TEXT    NOT NULL DEFAULT 'pending'
                             CHECK(status IN ('pending','claimed','completed','failed')),
    claimed_by       TEXT,
    claimed_at       TEXT,
    priority         INTEGER NOT NULL DEFAULT 0,
    payload          TEXT    NOT NULL DEFAULT '{}',
    created_at       TEXT    NOT NULL,
    completed_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_conv       ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_summarised ON messages(conversation_id, summarised);
CREATE INDEX IF NOT EXISTS idx_events_status       ON events(status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_events_conv         ON events(conversation_id);
CREATE INDEX IF NOT EXISTS idx_convs_status        ON conversations(status, last_activity_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_convs_external_id ON conversations(external_id)
    WHERE external_id IS NOT NULL;
"""


# ----------------------------------------------------------------------------------------------------
def init_db() -> None:
    global _wal_initialized
    with _conn() as c:
        # Migration first: add columns before executescript so indexes defined
        # in _SCHEMA can be created successfully.
        cols = {row[1] for row in c.execute("PRAGMA table_info(conversations)")}
        if cols and "external_id" not in cols:
            c.execute("ALTER TABLE conversations ADD COLUMN external_id TEXT")
        if cols and "input_history" not in cols:
            c.execute("ALTER TABLE conversations ADD COLUMN input_history TEXT NOT NULL DEFAULT '[]'")
        c.executescript(_SCHEMA)
    _wal_initialized = True


# ====================================================================================================
# MARK: HELPERS
# ====================================================================================================

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def _default_profile(channel_type: str) -> str:
    return _PROFILE_DEFAULTS.get(channel_type, _FALLBACK_PROFILE)


# ----------------------------------------------------------------------------------------------------
def _claimable_event_types_for_consumer(claimed_by: str) -> tuple[str, ...] | None:
    key = (claimed_by or "").strip().lower()
    return _CLAIMABLE_EVENT_TYPES.get(key)


# ====================================================================================================
# MARK: CONVERSATIONS
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def conversation_create(
    channel_type:       str,
    subject:            str | None = None,
    background_context: str        = "",
    profile:            str | None = None,
    external_id:        str | None = None,
) -> dict:
    now     = _now()
    profile = profile or _default_profile(channel_type)
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO conversations
                (channel_type, profile, status, subject, external_id, thread_summary, scratchpad,
                 background_context, token_estimate, turn_count,
                 last_activity_at, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (channel_type, profile, "active", subject, external_id, "", "{}", background_context, 0, 0, now, now, now),
        )
        row_id = cur.lastrowid
    return conversation_get(row_id)


# ----------------------------------------------------------------------------------------------------
def conversation_get_by_external_id(external_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM conversations WHERE external_id = ? LIMIT 1", (external_id,)
        ).fetchone()
    if row is None:
        return None
    result = _row_to_dict(row)
    try:
        result["scratchpad"] = json.loads(result["scratchpad"] or "{}")
    except json.JSONDecodeError:
        print(f"[database] Warning: malformed scratchpad JSON for external_id={external_id} - resetting to empty", flush=True)
        result["scratchpad"] = {}
    try:
        result["input_history"] = json.loads(result.get("input_history") or "[]")
    except json.JSONDecodeError:
        result["input_history"] = []
    return result


# ----------------------------------------------------------------------------------------------------
def conversation_get(conversation_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
    if row is None:
        return None
    result = _row_to_dict(row)
    try:
        result["scratchpad"] = json.loads(result["scratchpad"] or "{}")
    except json.JSONDecodeError:
        print(f"[database] Warning: malformed scratchpad JSON for conversation {conversation_id} - resetting to empty", flush=True)
        result["scratchpad"] = {}
    try:
        result["input_history"] = json.loads(result.get("input_history") or "[]")
    except json.JSONDecodeError:
        result["input_history"] = []
    return result


# ----------------------------------------------------------------------------------------------------
def conversation_get_turns_by_external_id(external_id: str) -> list[dict] | None:
    """Return messages for the conversation with the given external_id in a single DB connection.

    Returns None if no matching conversation exists.
    Returns an empty list if the conversation exists but has no messages.
    """
    with _conn() as c:
        conv_row = c.execute(
            "SELECT id FROM conversations WHERE external_id = ? LIMIT 1", (external_id,)
        ).fetchone()
        if conv_row is None:
            return None
        conv_id = conv_row["id"]
        msg_rows = c.execute(
            "SELECT direction, content FROM messages WHERE conversation_id = ? ORDER BY created_at ASC LIMIT 1000",
            (conv_id,),
        ).fetchall()
    return [{"direction": r["direction"], "content": r["content"]} for r in msg_rows]


# ----------------------------------------------------------------------------------------------------
def conversation_get_detail(conversation_id: int) -> dict | None:
    """Return conversation + messages + events in a single DB connection."""
    with _conn() as c:
        conv_row = c.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conv_row is None:
            return None
        conv = _row_to_dict(conv_row)
        try:
            conv["scratchpad"] = json.loads(conv["scratchpad"] or "{}")
        except json.JSONDecodeError:
            conv["scratchpad"] = {}
        try:
            conv["input_history"] = json.loads(conv.get("input_history") or "[]")
        except json.JSONDecodeError:
            conv["input_history"] = []
        msg_rows = c.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC LIMIT 500",
            (conversation_id,),
        ).fetchall()
        evt_rows = c.execute(
            "SELECT * FROM events WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 100",
            (conversation_id,),
        ).fetchall()
    return {
        "conversation": conv,
        "messages":     [_row_to_dict(r) for r in msg_rows],
        "events":       [_row_to_dict(r) for r in evt_rows],
    }


# ----------------------------------------------------------------------------------------------------
def conversation_get_with_messages(conversation_id: int) -> dict | None:
    conv = conversation_get(conversation_id)
    if conv is None:
        return None
    with _conn() as c:
        rows = c.execute(
            """
            SELECT * FROM messages
            WHERE conversation_id = ? AND summarised = 0
            ORDER BY created_at ASC
            """,
            (conversation_id,),
        ).fetchall()
    conv["messages"] = [_row_to_dict(r) for r in rows]
    return conv


# ----------------------------------------------------------------------------------------------------
def conversation_list(
    status:       str | None = None,
    channel_type: str | None = None,
    limit:        int        = 50,
    offset:       int        = 0,
) -> list[dict]:
    query  = "SELECT * FROM conversations WHERE 1=1"
    params: list = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if channel_type:
        query += " AND channel_type = ?"
        params.append(channel_type)
    query += " ORDER BY last_activity_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with _conn() as c:
        rows = c.execute(query, params).fetchall()
    result = []
    for row in rows:
        item = _row_to_dict(row)
        try:
            item["scratchpad"] = json.loads(item["scratchpad"] or "{}")
        except json.JSONDecodeError:
            print(f"[database] Warning: malformed scratchpad JSON for conversation {item.get('id')} - resetting to empty", flush=True)
            item["scratchpad"] = {}
        try:
            item["input_history"] = json.loads(item.get("input_history") or "[]")
        except json.JSONDecodeError:
            item["input_history"] = []
        result.append(item)
    return result


# ----------------------------------------------------------------------------------------------------
def conversation_update(
    conversation_id: int,
    status:           str | None  = None,
    subject:          str | None  = None,
    thread_summary:   str | None  = None,
    scratchpad:       dict | None = None,
    background_context: str | None = None,
    token_estimate:   int | None  = None,
    turn_count:       int | None  = None,
) -> dict | None:
    now    = _now()
    fields = ["updated_at = ?", "last_activity_at = ?"]
    params = [now, now]
    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if subject is not None:
        fields.append("subject = ?")
        params.append(subject)
    if thread_summary is not None:
        fields.append("thread_summary = ?")
        params.append(thread_summary)
    if scratchpad is not None:
        fields.append("scratchpad = ?")
        params.append(json.dumps(scratchpad))
    if background_context is not None:
        fields.append("background_context = ?")
        params.append(background_context)
    if token_estimate is not None:
        fields.append("token_estimate = ?")
        params.append(token_estimate)
    if turn_count is not None:
        fields.append("turn_count = ?")
        params.append(turn_count)
    params.append(conversation_id)
    with _conn() as c:
        c.execute(
            f"UPDATE conversations SET {', '.join(fields)} WHERE id = ?",
            params,
        )
    return conversation_get(conversation_id)


# ----------------------------------------------------------------------------------------------------
def conversation_get_input_history(conversation_id: int) -> list:
    with _conn() as c:
        row = c.execute(
            "SELECT input_history FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
    if row is None:
        return []
    try:
        return json.loads(row["input_history"] or "[]")
    except json.JSONDecodeError:
        return []


# ----------------------------------------------------------------------------------------------------
def conversation_set_input_history(conversation_id: int, history: list) -> None:
    now = _now()
    with _conn() as c:
        c.execute(
            "UPDATE conversations SET input_history = ?, updated_at = ? WHERE id = ?",
            (json.dumps(history), now, conversation_id),
        )


# ----------------------------------------------------------------------------------------------------
def conversation_delete(conversation_id: int) -> bool:
    with _conn() as c:
        c.execute("DELETE FROM events WHERE conversation_id = ?", (conversation_id,))
        cur = c.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    return cur.rowcount > 0


# ====================================================================================================
# MARK: MESSAGES
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def message_append(
    conversation_id: int,
    direction:       str,
    content:         str,
    sender_display:  str = "",
    status:          str = "received",
) -> dict:
    now = _now()
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO messages (conversation_id, direction, content, sender_display, status, summarised, created_at)
            VALUES (?,?,?,?,?,0,?)
            """,
            (conversation_id, direction, content, sender_display, status, now),
        )
        row_id = cur.lastrowid
        row    = c.execute("SELECT * FROM messages WHERE id = ?", (row_id,)).fetchone()
    return _row_to_dict(row)


# ----------------------------------------------------------------------------------------------------
def _latest_message_tx(c: sqlite3.Connection, conversation_id: int) -> sqlite3.Row | None:
    return c.execute(
        """
        SELECT id, direction, created_at FROM messages
        WHERE conversation_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (conversation_id,),
    ).fetchone()


# ----------------------------------------------------------------------------------------------------
def _conversation_has_unanswered_inbound_tx(c: sqlite3.Connection, conversation_id: int) -> bool:
    row = _latest_message_tx(c, conversation_id)
    return row is not None and row["direction"] == "inbound"


# ----------------------------------------------------------------------------------------------------
def conversation_has_unanswered_inbound(conversation_id: int) -> bool:
    with _conn() as c:
        return _conversation_has_unanswered_inbound_tx(c, conversation_id)


# ----------------------------------------------------------------------------------------------------
def ensure_response_needed_event(conversation_id: int) -> bool:
    """Atomically create a response_needed event if one does not already exist.

    Uses BEGIN IMMEDIATE to prevent a race between the existence check and the insert
    when two inbound messages arrive concurrently. Returns True if an event was created.
    """
    now = _now()
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        latest = _latest_message_tx(c, conversation_id)
        if latest is None or latest["direction"] != "inbound":
            c.execute("COMMIT")
            return False
        existing = c.execute(
            """
            SELECT 1 FROM events
            WHERE conversation_id = ?
              AND event_type = 'response_needed'
              AND status IN ('pending', 'claimed')
              AND created_at >= ?
            LIMIT 1
            """,
            (conversation_id, latest["created_at"]),
        ).fetchone()
        if existing:
            c.execute("COMMIT")
            return False
        c.execute(
            """
            INSERT INTO events (conversation_id, event_type, status, priority, payload, created_at)
            VALUES (?, 'response_needed', 'pending', 0, '{}', ?)
            """,
            (conversation_id, now),
        )
        c.execute("COMMIT")
    return True


# ----------------------------------------------------------------------------------------------------
def clear_pending_response_needed_events(conversation_id: int) -> int:
    now = _now()
    with _conn() as c:
        cur = c.execute(
            """
            UPDATE events
            SET status = 'completed', completed_at = ?
            WHERE conversation_id = ?
              AND event_type = 'response_needed'
              AND status IN ('pending', 'claimed')
            """,
            (now, conversation_id),
        )
    return cur.rowcount


# ----------------------------------------------------------------------------------------------------
def message_list(
    conversation_id: int,
    summarised:      int | None = None,
    direction:       str | None = None,
    limit:           int        = 200,
) -> list[dict]:
    query  = "SELECT * FROM messages WHERE conversation_id = ?"
    params: list = [conversation_id]
    if summarised is not None:
        query += " AND summarised = ?"
        params.append(summarised)
    if direction:
        query += " AND direction = ?"
        params.append(direction)
    query += " ORDER BY created_at ASC LIMIT ?"
    params.append(limit)
    with _conn() as c:
        rows = c.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


# ----------------------------------------------------------------------------------------------------
def message_update(
    message_id: int,
    status:     str | None = None,
    summarised: int | None = None,
) -> dict | None:
    fields = []
    params: list = []
    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if summarised is not None:
        fields.append("summarised = ?")
        params.append(summarised)
    if not fields:
        return None
    params.append(message_id)
    with _conn() as c:
        c.execute(f"UPDATE messages SET {', '.join(fields)} WHERE id = ?", params)
        row = c.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return _row_to_dict(row) if row else None


# ====================================================================================================
# MARK: EVENTS
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def event_create(
    conversation_id: int | None,
    event_type:      str,
    priority:        int  = 0,
    payload:         dict | None = None,
) -> dict:
    now = _now()
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO events (conversation_id, event_type, status, priority, payload, created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (
                conversation_id,
                event_type,
                "pending",
                priority,
                json.dumps(payload or {}),
                now,
            ),
        )
        row_id = cur.lastrowid
        row    = c.execute("SELECT * FROM events WHERE id = ?", (row_id,)).fetchone()
    return _row_to_dict(row)


# ----------------------------------------------------------------------------------------------------
def event_claim_next(claimed_by: str) -> dict | None:
    """Atomically claim the highest-priority pending event. Returns the event dict or None."""
    now = _now()
    claimable_types = _claimable_event_types_for_consumer(claimed_by)
    type_clause = ""
    type_params: list[str] = []
    if claimable_types:
        placeholders = ", ".join("?" for _ in claimable_types)
        type_clause = f" AND event_type IN ({placeholders})"
        type_params = list(claimable_types)
    with _conn() as c:
        # BEGIN IMMEDIATE gives write lock for the duration - prevents double-claim.
        c.execute("BEGIN IMMEDIATE")
        while True:
            row = c.execute(
                f"""
                SELECT * FROM events
                WHERE status = 'pending'
                {type_clause}
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """,
                type_params,
            ).fetchone()
            if row is None:
                c.execute("COMMIT")
                return None

            event_id = row["id"]
            conversation_id = row["conversation_id"]
            if (
                row["event_type"] == "response_needed"
                and conversation_id is not None
                and not _conversation_has_unanswered_inbound_tx(c, conversation_id)
            ):
                c.execute(
                    "UPDATE events SET status='completed', completed_at=? WHERE id=?",
                    (now, event_id),
                )
                continue

            c.execute(
                "UPDATE events SET status='claimed', claimed_by=?, claimed_at=? WHERE id=?",
                (claimed_by, now, event_id),
            )
            if row["event_type"] == "response_needed" and conversation_id is not None:
                c.execute(
                    "UPDATE conversations SET status='agent_processing', updated_at=?, last_activity_at=? WHERE id=?",
                    (now, now, conversation_id),
                )
            c.execute("COMMIT")
            updated = c.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            break
    return _row_to_dict(updated)


# ----------------------------------------------------------------------------------------------------
def event_complete(event_id: int, status: str = "completed") -> dict | None:
    now = _now()
    with _conn() as c:
        c.execute(
            "UPDATE events SET status=?, completed_at=? WHERE id=?",
            (status, now, event_id),
        )
        row = c.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return _row_to_dict(row) if row else None


# ----------------------------------------------------------------------------------------------------
def release_stale_claims() -> int:
    """Reset claimed events that have been held beyond CLAIM_TIMEOUT_SECS. Returns count released."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=CLAIM_TIMEOUT_SECS)
    cutoff_str = cutoff.isoformat()
    with _conn() as c:
        stale_response_conversations = [
            row["conversation_id"]
            for row in c.execute(
                """
                SELECT DISTINCT conversation_id
                FROM events
                WHERE status='claimed'
                  AND claimed_at < ?
                  AND event_type = 'response_needed'
                  AND conversation_id IS NOT NULL
                """,
                (cutoff_str,),
            ).fetchall()
        ]
        cur = c.execute(
            "UPDATE events SET status='pending', claimed_by=NULL, claimed_at=NULL "
            "WHERE status='claimed' AND claimed_at < ?",
            (cutoff_str,),
        )

        for conversation_id in stale_response_conversations:
            new_status = "waiting_agent" if _conversation_has_unanswered_inbound_tx(c, conversation_id) else "active"
            c.execute(
                "UPDATE conversations SET status=?, updated_at=?, last_activity_at=? WHERE id=?",
                (new_status, _now(), _now(), conversation_id),
            )
        return cur.rowcount


# ----------------------------------------------------------------------------------------------------
def event_list(
    conversation_id: int | None = None,
    status:          str | None = None,
    limit:           int        = 200,
) -> list[dict]:
    clauses: list[str] = []
    params:  list      = []
    if conversation_id is not None:
        clauses.append("conversation_id = ?")
        params.append(conversation_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM events {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ----------------------------------------------------------------------------------------------------
def event_counts() -> dict:
    with _conn() as c:
        rows = c.execute(
            "SELECT status, COUNT(*) as n FROM events GROUP BY status"
        ).fetchall()
    return {row["status"]: row["n"] for row in rows}


# ----------------------------------------------------------------------------------------------------
def conversation_counts() -> dict:
    with _conn() as c:
        rows = c.execute(
            "SELECT status, COUNT(*) as n FROM conversations GROUP BY status"
        ).fetchall()
    return {row["status"]: row["n"] for row in rows}


# ----------------------------------------------------------------------------------------------------
def clear_stale_outbound_ready(max_age_hours: int = 24) -> int:
    """Complete pending outbound_ready events older than max_age_hours.

    These accumulate when KoreComms is not running. Clearing them prevents the
    events table from growing unboundedly.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    cutoff_str = cutoff.isoformat()
    with _conn() as c:
        cur = c.execute(
            """
            UPDATE events
            SET status = 'completed', completed_at = ?
            WHERE event_type = 'outbound_ready'
              AND status = 'pending'
              AND created_at < ?
            """,
            (_now(), cutoff_str),
        )
    return cur.rowcount
