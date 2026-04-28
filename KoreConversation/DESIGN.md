# KoreConversation

## Purpose

KoreConversation is the shared conversation state service for the Kore system. It owns the canonical record of every conversation the agent is involved in, regardless of which channel the conversation originated from. Both MiniAgentFramework and KoreComms act on this service; neither owns conversation state directly.

---

## System Context

KoreConversation is the fourth co-operating service in the Kore stack:

| Service | Role | Port |
|---|---|---|
| **KoreConversation** | Shared conversation state, event queue (this service) | 8700 |
| **KoreData** | Reference knowledge, web scraping, Wikipedia clone | 8800 |
| **KoreComms** | External channel routing, delivery, inbox/outbox | 8900 |
| **MiniAgentFramework** | LLM execution, tool use, reasoning | 8000 |

---

## Design Principles

### Conversations are first-class domain objects

A conversation is not a log file, not a session, not a queue entry. It is a self-contained record holding everything needed to continue an exchange: who said what, what the agent has derived so far, calibrated summary for token efficiency, and standing background context.

### The agent is stateless between calls

MiniAgentFramework holds no conversation state in memory. Each time it processes a pending event it performs a clean read-process-write cycle:

1. Claim event from KoreConversation
2. GET conversation (summary + unsummarised messages + scratchpad)
3. Build prompt and run LLM
4. PATCH conversation (updated summary, scratchpad, token estimate)
5. POST outbound message
6. Complete event, raise next event if needed

Between calls, no conversation context lives inside MiniAgentFramework.

### Channel identity is private to KoreComms

KoreConversation knows only a `channel_type` string and the conversation ID. It never holds email addresses, phone numbers, OAuth tokens, or any channel-specific routing data. That all lives inside KoreComms. The agent never sees it either.

### Profiles define capability, not transport

Each conversation has a profile that determines what the agent is permitted to do. The profile defaults from the channel type but can be overridden per conversation.

Slash-command handling is intentionally narrower than general profile capability:
- all active chats are represented as KoreConversation conversations
- only prompts entered through the agent's main web chat panel are intercepted as slash commands
- slash commands are handled before the prompt is appended to KoreConversation
- slash-like text arriving through other KoreConversation channels is treated as ordinary message content

| Profile | Slash commands in main web chat | Tools | Debug output | Auto-compress |
|---|---|---|---|---|
| `admin` | yes | all | yes | no |
| `external` | no | limited set | no | yes |
| `readonly` | no | none | no | yes |

---

## Database Tables

### conversations

The central conversation record.

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | Stable across all services |
| channel_type | TEXT | 'webchat', 'gmail', 'sms', 'manual', 'whatsapp', etc. |
| profile | TEXT | 'admin', 'external', 'readonly' |
| status | TEXT | See status lifecycle below |
| subject | TEXT nullable | Email subject or equivalent |
| thread_summary | TEXT | LLM-compressed rolling context |
| scratchpad | TEXT (JSON) | Derived facts as key/value object |
| background_context | TEXT | Standing instructions for this contact/topic |
| token_estimate | INTEGER | Rough current prompt size, triggers compression |
| turn_count | INTEGER | Number of exchange rounds |
| last_activity_at | TEXT (ISO datetime) | Drives "most recent" ordering |
| created_at | TEXT | |
| updated_at | TEXT | |

Status values: `active`, `waiting_agent`, `agent_processing`, `archived`, `deleted`

Operational meaning:
- `active` means the latest message has been answered and the system is awaiting the next inbound message.
- `waiting_agent` means the latest message is inbound and still needs an agent response.
- `agent_processing` means the agent has claimed the work and is actively handling it.

Profile defaults by channel_type:
- `webchat` -> `admin`
- all others -> `external`

### messages

The raw append-only exchange log.

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| conversation_id | INTEGER FK | |
| direction | TEXT | 'inbound' or 'outbound' |
| content | TEXT | Message body |
| sender_display | TEXT | Human-readable label only, never an address |
| status | TEXT | 'received', 'draft', 'sent', 'failed' |
| summarised | INTEGER | 1 if folded into thread_summary |
| created_at | TEXT | |

### events

The coordination queue between MiniAgentFramework and KoreComms. Atomic claim prevents double-pickup.

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| conversation_id | INTEGER FK nullable | |
| event_type | TEXT | See event types below |
| status | TEXT | 'pending', 'claimed', 'completed', 'failed' |
| claimed_by | TEXT nullable | 'agent' or 'korecomms' - prevents double-pickup |
| claimed_at | TEXT nullable | Claim expiry: uncompleted claims older than 10 min are released |
| priority | INTEGER | 0 = normal, positive = higher priority |
| payload | TEXT (JSON) nullable | Event-specific data |
| created_at | TEXT | |
| completed_at | TEXT nullable | |

Event types:

| Type | Raised by | Consumed by |
|---|---|---|
| `response_needed` | KoreComms (inbound message arrived) | MiniAgentFramework |
| `outbound_ready` | MiniAgentFramework (draft reply written) | KoreComms |
| `compress_needed` | MiniAgentFramework (token_estimate threshold crossed) | MiniAgentFramework |
| `conversation_closed` | KoreComms (thread ended at channel level) | MiniAgentFramework |
| `conversation_deleted` | Either service | Both |

---

## REST API

### Conversations

| Method | Path | Description |
|---|---|---|
| POST | /conversations | Create a new conversation, returns {id, ...} |
| GET | /conversations | List conversations (query: status, channel_type, limit, offset) |
| GET | /conversations/{id} | Full record including unsummarised messages |
| PATCH | /conversations/{id} | Partial update: status, thread_summary, scratchpad, background_context, token_estimate, turn_count |
| DELETE | /conversations/{id} | Hard-delete the conversation row plus dependent events/messages |

### Messages

| Method | Path | Description |
|---|---|---|
| POST | /conversations/{id}/messages | Append a message |
| GET | /conversations/{id}/messages | List messages (query: summarised, direction, limit) |
| PATCH | /messages/{id} | Update status (e.g. draft -> sent) or mark summarised |

### Events

| Method | Path | Description |
|---|---|---|
| POST | /events | Create an event |
| GET | /events/next | Atomic claim: returns one pending event or 204. Query: claimed_by (required) |
| POST | /events/{id}/complete | Mark completed or failed |

### Status

| Method | Path | Description |
|---|---|---|
| GET | /status | Health check, returns version + counts |

---

## Read-Process-Write Cycle (Agent)

```
GET /events/next?claimed_by=agent
  -> 204: no work, sleep and retry
  -> 200: event claimed

GET /conversations/{conversation_id}
  -> thread_summary + scratchpad + background_context + unsummarised messages

Build prompt from conversation fields
Run LLM (orchestrate_prompt)

POST /conversations/{id}/messages  {direction: outbound, content: response}
PATCH /conversations/{id}          {thread_summary, scratchpad, token_estimate, turn_count, status}
POST /events/{event_id}/complete   {status: completed}

If response produced for a non-webchat, non-manual channel:
  POST /events  {conversation_id, event_type: outbound_ready}
```

---

## Thread Compression

When `token_estimate` crosses a threshold (default 70% of context window), the agent:

1. Fetches all unsummarised messages for the conversation
2. Runs a summarisation pass (delegate or direct prompt)
3. Appends result to `thread_summary`
4. Marks those messages as `summarised = 1` via PATCH /messages/{id}
5. Resets `token_estimate` to summary length only

Raw messages are never deleted - they remain for audit. Only `summarised` changes.

---

## Claim Timeout Reaper

A background thread runs every 60 seconds. Events with `status = 'claimed'` and `claimed_at` older than 10 minutes are reset to `status = 'pending'` so a crashed consumer does not permanently block a conversation.

---

## Relationship to chatsessions/

KoreConversation supersedes the historical `datacontrol/chatsessions/` directory used by MiniAgentFramework. That directory stored one JSON file per session containing turns, summaries, and scratchpad.

Under the new model:
- turns -> messages table
- summaries -> thread_summary field (single rolling compacted text)
- scratchpad -> scratchpad field
- session_id string -> conversation id integer

The old `chatsessions/` directory can remain on disk as historical data, but active runtime flows no longer write browser session state there. Session slash commands now operate through KoreConversation APIs and `external_id` mappings.

---

## Tech Stack

- Language: Python
- Framework: FastAPI
- Database: SQLite with WAL mode
- No external dependencies beyond fastapi and uvicorn
