from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Message and turn persistence helpers for KoreChat conversations.
#
# This module owns the write paths that mutate message history:
#   - append a single inbound/outbound message
#   - append a full inbound/outbound turn atomically
#   - detect whether a conversation still has an unanswered inbound message
#   - create or suppress response-needed events based on latest-message state
#
# Concurrency note:
#   - the multi-row turn and event checks use BEGIN IMMEDIATE so the "latest
#     message" view and the subsequent writes happen under one SQLite write lock.
# ====================================================================================================

import json
import sqlite3

from .db_common import _conn
from .db_common import _now
from .db_common import _row_to_dict
from .db_conversations import conversation_get


def message_append(
    conversation_id: int,
    direction: str,
    content: str,
    sender_display: str = "",
    status: str = "received",
) -> dict:
    now = _now()
    with _conn() as connection:
        cur = connection.execute(
            """
            INSERT INTO messages (conversation_id, direction, content, sender_display, status, summarised, created_at)
            VALUES (?,?,?,?,?,0,?)
            """,
            (conversation_id, direction, content, sender_display, status, now),
        )
        row = connection.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


def conversation_append_turn(
    conversation_id: int,
    inbound_content: str,
    outbound_content: str,
    inbound_sender: str = "",
    outbound_sender: str = "agent",
    token_estimate: int | None = None,
) -> dict | None:
    now = _now()
    with _conn() as connection:
        # Claim the write lock up front so turn_count, paired messages, and the
        # response-needed event transition are updated as one unit.
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT turn_count FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if row is None:
            connection.execute("COMMIT")
            return None

        connection.execute(
            """
            INSERT INTO messages (conversation_id, direction, content, sender_display, status, summarised, created_at)
            VALUES (?,?,?,?,?,0,?)
            """,
            (conversation_id, "inbound", inbound_content, inbound_sender, "received", now),
        )
        connection.execute(
            """
            INSERT INTO messages (conversation_id, direction, content, sender_display, status, summarised, created_at)
            VALUES (?,?,?,?,?,0,?)
            """,
            (conversation_id, "outbound", outbound_content, outbound_sender, "sent", now),
        )

        fields = ["turn_count = ?", "status = ?", "updated_at = ?", "last_activity_at = ?"]
        params: list[object] = [int(row["turn_count"] or 0) + 1, "active", now, now]
        if token_estimate is not None:
            fields.append("token_estimate = ?")
            params.append(token_estimate)
        params.append(conversation_id)
        connection.execute(f"UPDATE conversations SET {', '.join(fields)} WHERE id = ?", params)
        connection.execute(
            """
            UPDATE events
            SET status = 'completed', completed_at = ?
            WHERE conversation_id = ?
              AND event_type = 'response_needed'
              AND status IN ('pending', 'claimed')
            """,
            (now, conversation_id),
        )
        connection.execute("COMMIT")
    return conversation_get(conversation_id)


def _latest_message_tx(connection: sqlite3.Connection, conversation_id: int) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT id, direction, created_at FROM messages
        WHERE conversation_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (conversation_id,),
    ).fetchone()


def _conversation_has_unanswered_inbound_tx(connection: sqlite3.Connection, conversation_id: int) -> bool:
    row = _latest_message_tx(connection, conversation_id)
    return row is not None and row["direction"] == "inbound"


def conversation_has_unanswered_inbound(conversation_id: int) -> bool:
    with _conn() as connection:
        return _conversation_has_unanswered_inbound_tx(connection, conversation_id)


def ensure_response_needed_event(conversation_id: int, payload: dict | None = None) -> bool:
    now = _now()
    with _conn() as connection:
        # Keep the "latest inbound still unanswered?" check and the event insert in
        # the same transaction so concurrent writers do not enqueue duplicate work.
        connection.execute("BEGIN IMMEDIATE")
        latest = _latest_message_tx(connection, conversation_id)
        if latest is None or latest["direction"] != "inbound":
            connection.execute("COMMIT")
            return False
        existing = connection.execute(
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
            connection.execute("COMMIT")
            return False
        connection.execute(
            """
            INSERT INTO events (conversation_id, event_type, status, priority, payload, created_at)
            VALUES (?, 'response_needed', 'pending', 0, ?, ?)
            """,
            (conversation_id, json.dumps(payload or {}), now),
        )
        connection.execute("COMMIT")
    return True


def clear_pending_response_needed_events(conversation_id: int) -> int:
    now = _now()
    with _conn() as connection:
        cur = connection.execute(
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


def message_list(
    conversation_id: int,
    summarised: int | None = None,
    direction: str | None = None,
    limit: int = 200,
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
    with _conn() as connection:
        rows = connection.execute(query, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def message_update(message_id: int, status: str | None = None, summarised: int | None = None) -> dict | None:
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
    with _conn() as connection:
        connection.execute(f"UPDATE messages SET {', '.join(fields)} WHERE id = ?", params)
        row = connection.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return _row_to_dict(row) if row else None
