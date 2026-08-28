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
# MARK: FUNCTIONS
# Function inventory:
# - _normalise_tags: Implements the  normalise tags operation for this module.
# - _message_to_dict: Implements the  message to dict operation for this module.
# - message_append: Implements the message append operation for this module.
# - conversation_append_turn: Implements the conversation append turn operation for this module.
# - _latest_message_tx: Implements the  latest message tx operation for this module.
# - _conversation_has_unanswered_inbound_tx: Implements the  conversation has unanswered inbound tx operation for this module.
# - conversation_has_unanswered_inbound: Implements the conversation has unanswered inbound operation for this module.
# - ensure_response_needed_event: Ensures response needed event for this module.
# - clear_pending_response_needed_events: Clears pending response needed events for this module.
# - message_list: Implements the message list operation for this module.
# - message_update: Implements the message update operation for this module.
# ====================================================================================================

import json
import sqlite3

from .common import _conn
from .common import _decode_message_metadata
from .common import _now
from .common import _row_to_dict
from .conversations import conversation_get


def _normalise_tags(tags: list[str] | None, direction: str) -> list[str]:
    values = [direction]
    values.extend(tags or [])
    normalised: list[str] = []
    for value in values:
        tag = str(value or "").strip()
        if tag and tag not in normalised:
            normalised.append(tag)
    return normalised


def _message_to_dict(row: sqlite3.Row) -> dict:
    message = _row_to_dict(row)
    _decode_message_metadata(message)
    return message


def message_append(
    conversation_id: int,
    direction: str,
    content: str,
    sender_display: str = "",
    status: str = "received",
    delivery_eligible: bool = True,
    metadata: dict | None = None,
    tags: list[str] | None = None,
) -> dict:
    now = _now()
    message_metadata = {
        **(metadata or {}),
        "direction": direction,
        "sender_display": sender_display,
        "status": status,
        "delivery_eligible": bool(delivery_eligible),
        "tags": _normalise_tags(tags, direction),
        "created_at": now,
    }
    with _conn() as connection:
        cur = connection.execute(
            """
            INSERT INTO messages (conversation_id, content, metadata)
            VALUES (?,?,?)
            """,
            (conversation_id, content, json.dumps(message_metadata)),
        )
        row = connection.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _message_to_dict(row)


def conversation_append_turn(
    conversation_id:                int,
    inbound_content:                str,
    outbound_content:               str,
    inbound_sender:                 str = "",
    outbound_sender:                str = "agent",
    token_estimate:                 int | None = None,
    outbound_metadata:              dict | None = None,
    inbound_tags:                   list[str] | None = None,
    outbound_tags:                  list[str] | None = None,
    outbound_delivery_eligible: bool = True,
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

        inbound_metadata = {
            "direction": "inbound", "sender_display": inbound_sender,
            "status": "received", "delivery_eligible": True,
            "tags": _normalise_tags(inbound_tags, "inbound"), "created_at": now,
        }
        response_metadata = {
            **(outbound_metadata or {}), "direction": "outbound",
            "sender_display": outbound_sender, "status": "sent",
            "delivery_eligible": bool(outbound_delivery_eligible),
            "tags": _normalise_tags(outbound_tags, "outbound"), "created_at": now,
        }
        connection.execute(
            "INSERT INTO messages (conversation_id, content, metadata) VALUES (?,?,?)",
            (conversation_id, inbound_content, json.dumps(inbound_metadata)),
        )
        connection.execute(
            "INSERT INTO messages (conversation_id, content, metadata) VALUES (?,?,?)",
            (conversation_id, outbound_content, json.dumps(response_metadata)),
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
        SELECT id, metadata FROM messages
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (conversation_id,),
    ).fetchone()


def _conversation_has_unanswered_inbound_tx(connection: sqlite3.Connection, conversation_id: int) -> bool:
    row = _latest_message_tx(connection, conversation_id)
    return row is not None and _message_to_dict(row).get("direction") == "inbound"


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
        latest_message = _message_to_dict(latest) if latest is not None else None
        if latest_message is None or latest_message.get("direction") != "inbound":
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
            (conversation_id, latest_message.get("created_at") or now),
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
    direction: str | None = None,
    limit: int = 200,
) -> list[dict]:
    query  = "SELECT * FROM messages WHERE conversation_id = ?"
    params: list = [conversation_id]
    query += " ORDER BY id ASC"
    with _conn() as connection:
        rows = connection.execute(query, params).fetchall()
    messages = [_message_to_dict(row) for row in rows]
    if direction:
        messages = [message for message in messages if message.get("direction") == direction]
    return messages[:limit]


def message_update(
    message_id: int,
    status:     str | None = None,
    tags:       list[str] | None = None,
) -> dict | None:
    with _conn() as connection:
        row = connection.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            return None
        message = _message_to_dict(row)
        metadata = json.loads(message["metadata"])
        if status is not None:
            metadata["status"] = status
        if tags is not None:
            metadata["tags"] = _normalise_tags(tags, str(metadata.get("direction") or ""))
        if status is None and tags is None:
            return None
        connection.execute("UPDATE messages SET metadata = ? WHERE id = ?", (json.dumps(metadata), message_id))
        row = connection.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return _message_to_dict(row) if row else None
