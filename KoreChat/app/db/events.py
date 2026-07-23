from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Event-queue persistence for KoreChat background consumers.
#
# Owns:
#   - creating queue events tied to conversations
#   - claiming the next eligible event for a consumer
#   - completing or releasing events
#   - repairing stale claimed work after consumer timeouts
#
# Boundary:
#   - conversation/message CRUD stays in db_conversations.py and db_messages.py
#   - this file focuses on queue semantics and consumer coordination.
# ====================================================================================================

import json
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from .common import _claimable_event_types_for_consumer
from .common import _conn
from .common import _now
from .common import _row_to_dict
from .common import CLAIM_TIMEOUT_SECS
from .messages import _conversation_has_unanswered_inbound_tx


def event_create(
    conversation_id: int | None,
    event_type: str,
    priority: int = 0,
    payload: dict | None = None,
) -> dict:
    now = _now()
    with _conn() as connection:
        cur = connection.execute(
            """
            INSERT INTO events (conversation_id, event_type, status, priority, payload, created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (conversation_id, event_type, "pending", priority, json.dumps(payload or {}), now),
        )
        row = connection.execute("SELECT * FROM events WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


def event_claim_next(claimed_by: str) -> dict | None:
    now             = _now()
    claimable_types = _claimable_event_types_for_consumer(claimed_by)
    type_clause     = ""
    type_params: list[str] = []
    if claimable_types:
        placeholders = ", ".join("?" for _ in claimable_types)
        type_clause  = f" AND event_type IN ({placeholders})"
        type_params  = list(claimable_types)
    with _conn() as connection:
        # Claim under an immediate transaction so two consumers cannot observe the
        # same pending row and both proceed as if they own it.
        connection.execute("BEGIN IMMEDIATE")
        while True:
            row = connection.execute(
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
                connection.execute("COMMIT")
                return None

            event_id        = row["id"]
            conversation_id = row["conversation_id"]
            if (
                row["event_type"] == "response_needed"
                and conversation_id is not None
                and not _conversation_has_unanswered_inbound_tx(connection, conversation_id)
            ):
                # The inbound message was answered before this stale event reached the
                # head of the queue; retire it instead of handing pointless work off.
                connection.execute(
                    "UPDATE events SET status='completed', completed_at=? WHERE id=?",
                    (now, event_id),
                )
                continue

            connection.execute(
                "UPDATE events SET status='claimed', claimed_by=?, claimed_at=? WHERE id=?",
                (claimed_by, now, event_id),
            )
            if row["event_type"] == "response_needed" and conversation_id is not None:
                connection.execute(
                    "UPDATE conversations SET status='agent_processing', updated_at=?, last_activity_at=? WHERE id=?",
                    (now, now, conversation_id),
                )
            connection.execute("COMMIT")
            updated = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            break
    return _row_to_dict(updated)


def event_complete(event_id: int, status: str = "completed") -> dict | None:
    now = _now()
    with _conn() as connection:
        connection.execute("UPDATE events SET status=?, completed_at=? WHERE id=?", (status, now, event_id))
        row = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return _row_to_dict(row) if row else None


def release_stale_claims() -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=CLAIM_TIMEOUT_SECS)).isoformat()
    with _conn() as connection:
        stale_response_conversations = [
            row["conversation_id"]
            for row in connection.execute(
                """
                SELECT DISTINCT conversation_id
                FROM events
                WHERE status='claimed'
                  AND claimed_at < ?
                  AND event_type = 'response_needed'
                  AND conversation_id IS NOT NULL
                """,
                (cutoff,),
            ).fetchall()
        ]
        cur = connection.execute(
            "UPDATE events SET status='pending', claimed_by=NULL, claimed_at=NULL "
            "WHERE status='claimed' AND claimed_at < ?",
            (cutoff,),
        )
        for conversation_id in stale_response_conversations:
            new_status = "waiting_agent" if _conversation_has_unanswered_inbound_tx(connection, conversation_id) else "active"
            now = _now()
            connection.execute(
                "UPDATE conversations SET status=?, updated_at=?, last_activity_at=? WHERE id=?",
                (new_status, now, now, conversation_id),
            )
        return cur.rowcount


def event_list(conversation_id: int | None = None, status: str | None = None, limit: int = 200) -> list[dict]:
    clauses: list[str] = []
    params: list = []
    if conversation_id is not None:
        clauses.append("conversation_id = ?")
        params.append(conversation_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with _conn() as connection:
        rows = connection.execute(f"SELECT * FROM events {where} ORDER BY created_at DESC LIMIT ?", params).fetchall()
    return [_row_to_dict(row) for row in rows]


def event_counts() -> dict:
    with _conn() as connection:
        rows = connection.execute("SELECT status, COUNT(*) as n FROM events GROUP BY status").fetchall()
    return {row["status"]: row["n"] for row in rows}


def clear_stale_outbound_ready(max_age_hours: int = 24) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
    with _conn() as connection:
        cur = connection.execute(
            """
            UPDATE events
            SET status = 'completed', completed_at = ?
            WHERE event_type = 'outbound_ready'
              AND status = 'pending'
              AND created_at < ?
            """,
            (_now(), cutoff),
        )
    return cur.rowcount
