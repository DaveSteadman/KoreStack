from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Schema and forward-only migration helpers for KoreChat's SQLite store.
#
# Responsibilities:
#   - Define the canonical conversations / messages / events schema.
#   - Apply additive migrations for older databases before the canonical schema
#     script runs, so existing installs can move forward in place.
#   - Repair the legacy "__datasets" scratchpad embedding when the dedicated
#     datasets column is first introduced.
#
# This file deliberately does not expose CRUD helpers.  It exists to keep schema
# evolution separate from runtime read/write behaviour.
# ====================================================================================================

import json

from .db_common import _conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_type        TEXT    NOT NULL DEFAULT 'webchat',
    profile             TEXT    NOT NULL DEFAULT 'admin'
                                CHECK(profile IN ('admin','external','readonly')),
    status              TEXT    NOT NULL DEFAULT 'active'
                                CHECK(status IN ('active','waiting_agent','agent_processing','archived','deleted')),
    subject             TEXT,
    protected           INTEGER NOT NULL DEFAULT 0,
    external_id         TEXT,
    thread_summary      TEXT    NOT NULL DEFAULT '',
    scratchpad          TEXT    NOT NULL DEFAULT '{}',
    datasets            TEXT    NOT NULL DEFAULT '{}',
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


def init_db() -> None:
    with _conn() as connection:
        cols = {row[1] for row in connection.execute("PRAGMA table_info(conversations)")}
        if cols and "external_id" not in cols:
            connection.execute("ALTER TABLE conversations ADD COLUMN external_id TEXT")
        if cols and "input_history" not in cols:
            connection.execute("ALTER TABLE conversations ADD COLUMN input_history TEXT NOT NULL DEFAULT '[]'")
        if cols and "datasets" not in cols:
            connection.execute("ALTER TABLE conversations ADD COLUMN datasets TEXT NOT NULL DEFAULT '{}'")
            rows = connection.execute("SELECT id, scratchpad FROM conversations").fetchall()
            for row in rows:
                raw_scratchpad = str(row["scratchpad"] or "{}")
                try:
                    scratchpad_payload = json.loads(raw_scratchpad)
                except json.JSONDecodeError:
                    continue
                if not isinstance(scratchpad_payload, dict):
                    continue
                datasets_payload = scratchpad_payload.pop("__datasets", None)
                if not isinstance(datasets_payload, dict):
                    continue
                # Migrate the old embedded datasets payload into the dedicated column
                # once, while leaving unrelated scratchpad keys intact.
                connection.execute(
                    "UPDATE conversations SET scratchpad = ?, datasets = ? WHERE id = ?",
                    (json.dumps(scratchpad_payload), json.dumps(datasets_payload), row["id"]),
                )
        if cols and "protected" not in cols:
            connection.execute("ALTER TABLE conversations ADD COLUMN protected INTEGER NOT NULL DEFAULT 0")
            if "has_explicit_name" in cols:
                connection.execute("UPDATE conversations SET protected = coalesce(has_explicit_name, 0)")
            connection.execute(
                """
                UPDATE conversations
                SET protected = CASE
                    WHEN lower(trim(coalesce(subject, ''))) IN ('', 'new conversation') THEN 0
                    WHEN lower(trim(coalesce(subject, ''))) = lower('webchat ' || substr(coalesce(external_id, ''), 9))
                         AND lower(coalesce(external_id, '')) LIKE 'webchat_%' THEN 0
                    ELSE 1
                END
                """
            )
        elif cols:
            connection.execute(
                """
                UPDATE conversations
                SET protected = 0
                WHERE lower(trim(coalesce(subject, ''))) = lower('webchat ' || substr(coalesce(external_id, ''), 9))
                  AND lower(coalesce(external_id, '')) LIKE 'webchat_%'
                """
            )
        connection.executescript(_SCHEMA)
