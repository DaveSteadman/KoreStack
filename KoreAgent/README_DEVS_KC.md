# KoreConversation - Developer Notes

For the main framework developer notes see [README_DEVS.md](README_DEVS.md).

---

## Purpose

KoreConversation (KC) is the shared conversation state service for the Kore system. It owns the canonical record of every conversation the agent is involved in, regardless of which channel the conversation originated from.

Neither MiniAgentFramework nor KoreComms owns conversation state directly. Both act on KC. The agent is stateless between calls - it reads from KC, processes, and writes back. No conversation context lives inside MiniAgentFramework between turns.

---

## Role in the Kore Stack

| Service | Role | Port |
|---|---|---|
| KoreConversation | Shared conversation state, event queue | 8700 |
| KoreData | Reference knowledge, web scraping, Wikipedia clone | 8800 |
| KoreComms | External channel routing, delivery, inbox/outbox | 8900 |
| MiniAgentFramework | LLM execution, tool use, reasoning | 8000 |

KC is the hub that decouples the agent from channel specifics. KoreComms routes an inbound message from any channel into a KC conversation. MiniAgentFramework polls KC for work, processes it, and writes the reply back. KoreComms then picks up the reply and delivers it to the channel.

The agent never sees email addresses, phone numbers, OAuth tokens, or any channel-specific routing data. That all lives inside KoreComms.

---

## Code Layout

```
code/KoreConversation/
  main.py              Entry point - launches uvicorn
  requirements.txt
  DESIGN.md            Original design document
  config/
    default.json       Config overrides (port, host, log_level, data_dir)
  app/
    __init__.py
    api.py             FastAPI application - routes, SSE broadcaster, reaper thread
    database.py        SQLite data-access layer - all queries live here
    config.py          Config loader
    logutil.py         uvicorn log config builder
    version.py         __version__
    ui/
      conversations.html   Debug UI page
      conversations.js     Vanilla JS - renders conversation list and detail panel
      conversations.css
```

MAF-side integration lives at:

```
code/KoreAgent/input_layer/
  koreconv_input.py    Background polling thread, prompt builder, event handler
  koreconv_client.py   Process lifecycle helpers (start/stop the KC process)
```

---

## Database

KC uses SQLite with WAL mode. The database file is created at startup at `data_dir/koreconversation.db`.

Default `data_dir` is `<repo_root>/datacontrol/conversations/`. This is configured in `config/default.json` or falls back to the built-in default in `app/config.py`.

### conversations

The central conversation record. One row per active or historical exchange.

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | Stable across all services |
| channel_type | TEXT | webchat, gmail, sms, manual, whatsapp, etc. |
| profile | TEXT | admin, external, readonly |
| status | TEXT | See status lifecycle below |
| subject | TEXT nullable | Email subject or equivalent label |
| external_id | TEXT nullable | Unique external identifier (e.g. email thread ID) |
| thread_summary | TEXT | LLM-compressed rolling context from prior turns |
| scratchpad | TEXT (JSON) | Derived facts as a key/value object |
| input_history | TEXT (JSON) | Browser input recall entries for the webchat UI |
| background_context | TEXT | Standing instructions for this contact or topic |
| token_estimate | INTEGER | Rough current prompt size in tokens |
| turn_count | INTEGER | Number of completed exchange rounds |
| last_activity_at | TEXT (ISO) | Drives most-recent ordering |
| created_at | TEXT (ISO) | |
| updated_at | TEXT (ISO) | |

#### Status lifecycle

| Status | Meaning |
|---|---|
| active | Last message has been answered. Waiting for next inbound. |
| waiting_agent | Inbound message received. Agent has not yet claimed it. |
| agent_processing | Agent has claimed the event and is actively running. |
| archived | Conversation closed. No further processing expected. |
| deleted | Soft-deleted marker. Hard DELETE cascades messages and events. |

Profile defaults by channel_type: `webchat` -> `admin`, all others -> `external`.

### messages

Append-only exchange log. Messages are never deleted. The `summarised` flag marks those already folded into `thread_summary`.

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| conversation_id | INTEGER FK | References conversations(id) ON DELETE CASCADE |
| direction | TEXT | inbound or outbound |
| content | TEXT | Message body |
| sender_display | TEXT | Human-readable label only - never an address or routing data |
| status | TEXT | received, draft, sent, failed |
| summarised | INTEGER | 1 if folded into thread_summary |
| created_at | TEXT (ISO) | |

### events

The coordination queue between MiniAgentFramework and KoreComms. Atomic claim (via `BEGIN IMMEDIATE`) prevents double-pickup.

| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| conversation_id | INTEGER FK nullable | References conversations(id) ON DELETE SET NULL |
| event_type | TEXT | See event types below |
| status | TEXT | pending, claimed, completed, failed |
| claimed_by | TEXT nullable | "agent" or "korecomms" |
| claimed_at | TEXT nullable | Used by the reaper to detect stale claims |
| priority | INTEGER | 0 = normal, positive = higher. Higher priority is claimed first. |
| payload | TEXT (JSON) | Event-specific data. Most events use an empty object. |
| created_at | TEXT (ISO) | |
| completed_at | TEXT (ISO) nullable | |

#### Event types

| Type | Raised by | Consumed by | Meaning |
|---|---|---|---|
| response_needed | KC (on inbound message) | MiniAgentFramework | An inbound message needs an agent reply |
| outbound_ready | MiniAgentFramework | KoreComms | A reply is ready for delivery to the channel |
| compress_needed | MiniAgentFramework | MiniAgentFramework | Token estimate is high - run thread compression |
| conversation_closed | KoreComms | MiniAgentFramework | Channel-level thread has ended |
| conversation_deleted | Either | Both | Conversation is being removed |

Each consumer only claims its own event types. The mapping lives in `database.py` `_CLAIMABLE_EVENT_TYPES`.

---

## REST API

### Conversations

| Method | Path | Description |
|---|---|---|
| POST | /conversations | Create a new conversation |
| GET | /conversations | List conversations (query: status, channel_type, limit, offset) |
| GET | /conversations/{id} | Full record with unsummarised messages embedded |
| GET | /conversations/{id}/detail | Full record + messages + events in one response (used by debug UI) |
| PATCH | /conversations/{id} | Partial update of any conversation field |
| DELETE | /conversations/{id} | Hard-delete the conversation row, cascades messages and events |
| GET | /conversations/by-external-id/{external_id} | Lookup by external_id |
| GET | /conversations/by-external-id/{external_id}/turns | Raw inbound/outbound messages for that conversation |
| GET | /conversations/{id}/input-history | Return stored input recall entries |
| PATCH | /conversations/{id}/input-history | Append one entry (deduped, capped at 32) |

### Messages

| Method | Path | Description |
|---|---|---|
| POST | /conversations/{id}/messages | Append a message. Inbound triggers response_needed. Outbound clears pending response_needed. |
| GET | /conversations/{id}/messages | List messages (query: summarised, direction, limit) |
| PATCH | /messages/{id} | Update status or mark summarised |

### Events

| Method | Path | Description |
|---|---|---|
| POST | /events | Create an event |
| GET | /events/next | Atomic claim - returns one pending claimable event or 204 (query: claimed_by required) |
| GET | /events | List events (query: conversation_id, status, limit) |
| POST | /events/{id}/complete | Mark completed or failed |

### Status

| Method | Path | Description |
|---|---|---|
| GET | /status | Returns version and row counts for conversations and events |

### Debug UI

| Method | Path | Description |
|---|---|---|
| GET | /ui | Conversations debug page |

### SSE Stream

| Method | Path | Description |
|---|---|---|
| GET | /stream | Subscribe to live change notifications |

---

## Event Claim Mechanics

`GET /events/next?claimed_by=agent` is an atomic operation. The database function `event_claim_next` opens a `BEGIN IMMEDIATE` transaction and selects the next pending event whose type is claimable by the named consumer, ordered by `(priority DESC, created_at ASC)`. It sets `status = 'claimed'`, `claimed_by`, and `claimed_at` in the same transaction before returning. No other caller can claim the same row.

The response from `/events/next` is enriched before returning: if the event has a `conversation_id`, `conversation_get_with_messages` is called and the full conversation record (plus its unsummarised messages) is embedded under `"conversation"` in the response. The consumer therefore gets everything it needs in one HTTP call.

---

## SSE Push Stream

Any mutation endpoint (`POST /conversations`, `PATCH /conversations/{id}`, `POST /conversations/{id}/messages`, `DELETE /conversations/{id}`, `POST /events/{id}/complete`) calls `_kc_push()` after committing. `_kc_push` broadcasts a small JSON event to all connected `/stream` clients via in-process queues.

The debug UI subscribes to `/stream` on load and updates without polling. A 20-second keepalive comment is sent to keep the connection alive through proxies and browser timeouts.

---

## Stale Claim Reaper

A background thread runs every 60 seconds (`_reaper_loop` in `api.py`). It calls `database.release_stale_claims()` which resets any `status = 'claimed'` event whose `claimed_at` is older than `CLAIM_TIMEOUT_SECS` (10 minutes) back to `status = 'pending'`. This ensures a crashed consumer cannot permanently block a conversation.

---

## Thread Compression

When `token_estimate` crosses a threshold, a `compress_needed` event is raised. MiniAgentFramework claims it and:

1. Fetches all unsummarised messages for the conversation
2. Runs a summarisation pass (delegate or direct prompt)
3. Appends the result to `thread_summary`
4. Marks those messages `summarised = 1` via PATCH /messages/{id}
5. Resets `token_estimate` to the summary length only

Raw messages are never deleted. Only the `summarised` flag changes.

---

## Profile and Capability Model

Each conversation carries a profile that determines what the agent is permitted to do.

| Profile | Tools | Debug output | Auto-compress |
|---|---|---|---|
| admin | all | yes | no |
| external | limited set | no | yes |
| readonly | none | no | yes |

Profiles are a KC concept. MiniAgentFramework reads the profile from the conversation record and adjusts the tool catalog and output behaviour accordingly. Channel type sets the default profile; it can be overridden per conversation.

---

## MAF Integration

### Configuration

`koreconvurl` in `default.json` at the repo root sets the KC base URL (e.g. `http://localhost:8700`). If the key is absent the KC integration thread exits immediately and logs a notice. No other configuration is required.

### Background Polling Thread

`koreconv_input.start_koreconv_loop()` is called by `api_mode.py` at MAF startup alongside the scheduler thread. It runs a tight loop:

1. Call `GET /events/next?claimed_by=agent`
2. If 204 - no work, sleep `_DEFAULT_POLL_SECS` (3 seconds) and retry
3. If 200 - enqueue the event onto the shared `task_queue` under the name `kc_event_{id}`

Enqueueing onto `task_queue` serialises KC work with browser prompts and scheduled tasks. The same orchestration runtime handles all three.

### Event Handler

`_handle_event` runs the full lifecycle for one `response_needed` event:

```
1. Restore scratchpad from conv.scratchpad into the MAF session store
2. Build prompt via _build_prompt(conv, messages)
3. make_task_session(session_id="kc_conv_{id}", persist_path=None)
4. orchestrate_prompt(user_prompt, ...)
5. POST /conversations/{id}/messages  {direction: outbound, content: reply}
6. PATCH /conversations/{id}          {status: active, token_estimate, turn_count, scratchpad}
7. POST /events/{event_id}/complete   {status: completed}
8. POST /events  {event_type: outbound_ready}  (only for non-webchat, non-manual channels)
```

The session created in step 3 uses `persist_path=None` - KC owns the persistent state, MAF holds only transient per-run context.

Other event types that arrive (compress_needed, conversation_closed, etc.) are completed immediately with no action until the relevant handling is implemented.

### Prompt Structure

`_build_prompt` assembles the user-turn prompt from the conversation fields. Only non-empty sections are included, in this order:

```
--- Background context ---
{background_context}

--- Prior conversation summary ---
{thread_summary}

--- Scratchpad ---
  key: value
  ...

--- Conversation ---
[timestamp] User (sender): content
[timestamp] Agent: content
...

--- Respond to this message ---
{last inbound message content}
```

Only unsummarised messages appear in the `--- Conversation ---` block. Summarised messages are already folded into `thread_summary`. The `--- Respond to this message ---` tail is the literal last inbound message, repeated as a clear directive.

### Scratchpad Round-Trip

Before the run:
- `scratch_clear(session_id)` clears any leftover in-memory state for this session
- Each key from `conv.scratchpad` is loaded via `scratch_save(key, value, session_id)`

After the run:
- `get_store(session_id)` returns the current in-memory scratchpad as a dict
- It is written back to KC via `PATCH /conversations/{id}` in the `scratchpad` field

This is the only statefulness that crosses the MAF/KC boundary per turn. KC owns the durable copy.

### Session Naming

KC conversations map to MAF sessions with the stable prefix `kc_conv_{id}`. Because `persist_path=None` is used, MAF writes no session JSON file. The session context is purely transient for the duration of the run.

---

## Debug UI

The debug UI at `http://localhost:8700/ui` provides a read-only view of all conversations and their full state. It is useful for inspecting the database state directly without writing SQL.

It uses a stale-while-revalidate pattern: on load it renders immediately from `localStorage` cache, then issues parallel fetches for fresh data. Once connected, the `/stream` SSE push stream keeps the list updated without polling.

The right-hand detail pane shows conversation metadata, background context, thread summary, scratchpad, the full message list, and recent events - fetched in one call from `GET /conversations/{id}/detail`.

The sidebar width is resizable via drag.

---

## Relationship to datacontrol/chatsessions/

The historical `datacontrol/chatsessions/` directory stored one JSON file per browser session containing turns, summary, and scratchpad. KoreConversation supersedes this:

| Old | New |
|---|---|
| One JSON file per session | One row in conversations table |
| Turns stored in JSON array | Messages table (append-only) |
| Single summary string in JSON | thread_summary field |
| Scratchpad dict in JSON | scratchpad field |
| session_id string | conversation id integer |

The old directory can remain on disk as historical data. Active runtime flows no longer write browser session state there. Session slash commands in the web UI operate through KC APIs and `external_id` mappings.

---

## Running KC Standalone

```
cd code/KoreConversation
python main.py
```

Or directly via uvicorn:

```
uvicorn app.api:app --host 0.0.0.0 --port 8700
```

Config overrides go in `code/KoreConversation/config/default.json`. All keys are optional.

```json
{
    "port": 8700,
    "host": "0.0.0.0",
    "log_level": "info",
    "data_dir": "../../datacontrol/conversations"
}
```

A relative `data_dir` is resolved against the repo root.

---

## Suggested Reading Order

1. [DESIGN.md](code/KoreConversation/DESIGN.md) - original design intent and principles
2. [app/database.py](code/KoreConversation/app/database.py) - schema, claim mechanics, all queries
3. [app/api.py](code/KoreConversation/app/api.py) - routes, SSE broadcaster, reaper thread
4. [app/config.py](code/KoreConversation/app/config.py) - config loading
5. [code/KoreAgent/input_layer/koreconv_input.py](code/KoreAgent/input_layer/koreconv_input.py) - MAF polling loop, event handler, prompt builder
