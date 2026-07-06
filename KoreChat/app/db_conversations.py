from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Conversation-oriented KoreChat database operations.
#
# Owns:
#   - conversation creation / lookup by id or external id
#   - detail views that join conversations with messages and events
#   - subject / profile / protected-field updates
#   - session-state decoding for scratchpad, datasets, and input-history payloads
#
# Boundary:
#   - message append / turn persistence lives in db_messages.py
#   - event queue semantics live in db_events.py
# ====================================================================================================

import json
import sqlite3
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from .db_common import _conn
from .db_common import _decode_session_state_fields
from .db_common import _default_profile
from .db_common import _is_protected_subject
from .db_common import _now
from .db_common import _row_to_dict


def conversation_create(
    channel_type: str,
    subject: str | None = None,
    background_context: str = "",
    profile: str | None = None,
    external_id: str | None = None,
    protected: bool | None = None,
    tools_active: list[str] | None = None,
) -> dict:
    now             = _now()
    profile         = profile or _default_profile(channel_type)
    protected_value = int(protected) if protected is not None else _is_protected_subject(subject, external_id)
    tools_active_payload = json.dumps(tools_active if isinstance(tools_active, list) else [])
    with _conn() as connection:
        try:
            cur = connection.execute(
                """
                INSERT INTO conversations
                    (channel_type, profile, status, subject, protected, external_id, thread_summary, scratchpad, datasets,
                     tools_active, background_context, token_estimate, turn_count,
                     last_activity_at, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (channel_type, profile, "active", subject, protected_value, external_id, "", "{}", "{}", tools_active_payload, background_context, 0, 0, now, now, now),
            )
            row_id = cur.lastrowid
        except sqlite3.IntegrityError:
            if external_id is None:
                raise
            existing = connection.execute(
                "SELECT id FROM conversations WHERE external_id = ? LIMIT 1",
                (external_id,),
            ).fetchone()
            if existing is None:
                raise
            row_id = existing["id"]
    return conversation_get(row_id)


def conversation_get_by_external_id(external_id: str) -> dict | None:
    with _conn() as connection:
        row = connection.execute("SELECT * FROM conversations WHERE external_id = ? LIMIT 1", (external_id,)).fetchone()
    if row is None:
        return None
    result = _row_to_dict(row)
    _decode_session_state_fields(result, label=f"conversation external_id={external_id}")
    return result


def conversation_get(conversation_id: int) -> dict | None:
    with _conn() as connection:
        row = connection.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if row is None:
        return None
    result = _row_to_dict(row)
    _decode_session_state_fields(result, label=f"conversation {conversation_id}")
    return result


def conversation_get_turns_by_external_id(external_id: str) -> list[dict] | None:
    with _conn() as connection:
        conv_row = connection.execute("SELECT id FROM conversations WHERE external_id = ? LIMIT 1", (external_id,)).fetchone()
        if conv_row is None:
            return None
        msg_rows = connection.execute(
            "SELECT direction, content FROM messages WHERE conversation_id = ? ORDER BY created_at ASC LIMIT 1000",
            (conv_row["id"],),
        ).fetchall()
    return [{"direction": row["direction"], "content": row["content"]} for row in msg_rows]


def conversation_get_detail(conversation_id: int) -> dict | None:
    with _conn() as connection:
        conv_row = connection.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if conv_row is None:
            return None
        conv = _row_to_dict(conv_row)
        _decode_session_state_fields(conv, label=f"conversation {conversation_id}")
        msg_rows = connection.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC LIMIT 500",
            (conversation_id,),
        ).fetchall()
        evt_rows = connection.execute(
            "SELECT * FROM events WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 100",
            (conversation_id,),
        ).fetchall()
    return {
        "conversation": conv,
        "messages":     [_row_to_dict(row) for row in msg_rows],
        "events":       [_row_to_dict(row) for row in evt_rows],
    }


def conversation_get_with_messages(conversation_id: int) -> dict | None:
    conv = conversation_get(conversation_id)
    if conv is None:
        return None
    with _conn() as connection:
        rows = connection.execute(
            """
            SELECT * FROM messages
            WHERE conversation_id = ? AND summarised = 0
            ORDER BY created_at ASC
            """,
            (conversation_id,),
        ).fetchall()
    conv["messages"] = [_row_to_dict(row) for row in rows]
    return conv


def conversation_list(
    status: str | None = None,
    channel_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
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
    with _conn() as connection:
        rows = connection.execute(query, params).fetchall()
    result = []
    for row in rows:
        item = _row_to_dict(row)
        _decode_session_state_fields(item, label=f"conversation {item.get('id')}")
        result.append(item)
    return result


def conversation_update(
    conversation_id: int,
    status: str | None = None,
    subject: str | None = None,
    protected: bool | None = None,
    thread_summary: str | None = None,
    scratchpad: dict | None = None,
    datasets: dict | None = None,
    tools_active: list[str] | None = None,
    background_context: str | None = None,
    token_estimate: int | None = None,
    turn_count: int | None = None,
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
        if _is_protected_subject(subject):
            fields.append("protected = 1")
    if protected is not None:
        fields.append("protected = ?")
        params.append(int(protected))
    if thread_summary is not None:
        fields.append("thread_summary = ?")
        params.append(thread_summary)
    if scratchpad is not None:
        fields.append("scratchpad = ?")
        params.append(json.dumps(scratchpad))
    if datasets is not None:
        fields.append("datasets = ?")
        params.append(json.dumps(datasets))
    if tools_active is not None:
        fields.append("tools_active = ?")
        params.append(json.dumps(tools_active))
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
    with _conn() as connection:
        connection.execute(f"UPDATE conversations SET {', '.join(fields)} WHERE id = ?", params)
    return conversation_get(conversation_id)


def conversation_get_input_history(conversation_id: int) -> list:
    with _conn() as connection:
        row = connection.execute("SELECT input_history FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if row is None:
        return []
    try:
        return json.loads(row["input_history"] or "[]")
    except json.JSONDecodeError:
        return []


def conversation_set_input_history(conversation_id: int, history: list) -> None:
    now = _now()
    with _conn() as connection:
        connection.execute(
            "UPDATE conversations SET input_history = ?, updated_at = ? WHERE id = ?",
            (json.dumps(history), now, conversation_id),
        )


def conversation_append_input_history(conversation_id: int, text: str, max_entries: int) -> list[str]:
    now = _now()
    with _conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT input_history FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if row is None:
            connection.execute("COMMIT")
            return []
        try:
            entries = json.loads(row["input_history"] or "[]")
        except json.JSONDecodeError:
            entries = []
        if not isinstance(entries, list):
            entries = []
        entries = [entry for entry in entries if entry != text]
        entries.append(text)
        if len(entries) > max_entries:
            entries = entries[-max_entries:]
        connection.execute(
            "UPDATE conversations SET input_history = ?, updated_at = ? WHERE id = ?",
            (json.dumps(entries), now, conversation_id),
        )
        connection.execute("COMMIT")
    return entries


def conversation_delete(conversation_id: int) -> bool:
    with _conn() as connection:
        connection.execute("DELETE FROM events WHERE conversation_id = ?", (conversation_id,))
        cur = connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    return cur.rowcount > 0


def conversation_cull_default_inactive(max_default_chat_age_days: int) -> list[int]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_default_chat_age_days)).isoformat()
    with _conn() as connection:
        rows = connection.execute(
            """
            SELECT id
            FROM conversations
            WHERE coalesce(last_activity_at, created_at) <= ?
              AND coalesce(protected, 0) = 0
            """,
            (cutoff,),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if not ids:
            return []
        connection.executemany("DELETE FROM events WHERE conversation_id = ?", [(cid,) for cid in ids])
        connection.executemany("DELETE FROM conversations WHERE id = ?", [(cid,) for cid in ids])
    return ids


def conversation_counts() -> dict:
    with _conn() as connection:
        rows = connection.execute("SELECT status, COUNT(*) as n FROM conversations GROUP BY status").fetchall()
    return {row["status"]: row["n"] for row in rows}
