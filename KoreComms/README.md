# KoreComms

> External communication hub for [KoreAgent](../KoreAgent) — owns external-channel routing in its own SQLite database and bridges those conversations to KoreChat by stable local conversation names.

![KoreComms chat interface](progress/Screenshot_13-4-2026_223926_localhost.jpeg)

---

## Overview

KoreComms is one of three co-operating local services:

| Service | Role |
|---|---|
| [KoreData](../KoreData) | Data provider — web scraping, Wikipedia clone |
| [KoreAgent](../KoreAgent) | LLM wrapper with context and orchestration |
| **KoreComms** | External communication hub (this service) |

The agent never talks to Gmail, Outlook, or any other channel directly. KoreComms owns all that complexity while KoreChat owns canonical thread state and cross-service events.

---

## Features

- **Event-driven coordination** — inbound messages become KoreChat events and outbound delivery is triggered from `outbound_ready` events instead of full-thread scans
- **Full conversation threading** — conversation state and message history live in KoreChat, and KoreComms reads the canonical thread on demand
- **Local-first conversation identity** — KoreComms keeps its own conversation rows and uses a stable `conversation_name` to find or recreate the matching agent-side conversation
- **Chat UI** — per-conversation view with event-driven live updates, command-style input history on `Up` / `Down`, and a compose bar (`Enter` to send, `Shift+Enter` for new line)
- **Discord integration** — bot-token polling for configured channels or threads, de-duplication by Discord message ID, and reply routing back into the same channel
- **Gmail integration** — OAuth2 polling, reply-in-thread, de-duplication by Gmail message ID
- **Manual interface** — inject a synthetic message via the WebUI; always present, zero external dependencies
- **Adapter pattern** — each interface type lives in its own package under `app/interfaces/`, with shared abstractions in `app/interfaces/common/`
- **Credentials encrypted at rest** — OAuth tokens and API secrets stored with `cryptography` (Fernet)
- **Dark amber terminal UI** — monospace, minimal, consistent with the KoreData / KoreAgent aesthetic

---

## Tech Stack

- **Python 3.11+** with FastAPI + Uvicorn
- **SQLite** (WAL mode, per-call connections)
- **Jinja2** templates (server-rendered WebUI)
- **google-api-python-client** for Gmail
- **cryptography** for at-rest encryption
- **Discord REST API** via the Python standard library for Discord bot access

---

## Quick Start

```powershell
# Clone and enter the repo
cd C:\Util\GithubRepos\KoreComms

# Create a virtual environment and install dependencies
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# Start the server
py main.py
```

The WebUI is at **http://localhost:8625**.

---

## Configuration

Edit `config/default.json` (created automatically on first run with defaults):

```json
{
  "host": "0.0.0.0",
   "port": 8625,
  "log_level": "info",
  "poll_interval": 60,
   "event_poll_interval": 1.0,
   "missing_kc_conversation_policy": "recreate",
   "data_dir": "datacontrol/korecomms",
   "korechat_url": "http://localhost:8630"
}
```

| Key | Default | Description |
|---|---|---|
| `host` | `0.0.0.0` | Bind address |
| `port` | `8625` | HTTP port |
| `poll_interval` | `60` | Gmail poll interval in seconds |
| `event_poll_interval` | `1.0` | How often KoreComms checks KoreChat for outbound delivery events |
| `missing_kc_conversation_policy` | `recreate` | What to do if the linked KoreChat record is gone: `recreate` or `abort` |
| `data_dir` | `datacontrol/korecomms` | SQLite database directory under the shared suite control tree |
| `korechat_url` | `http://localhost:8630` | KoreChat base URL for outbound delivery events |

Discord connection settings live per interface in the WebUI:

- `bot_token`: encrypted at rest; used for Discord REST API calls
- `channel_ids`: list of channel or thread IDs to poll for inbound messages

For `POST /api/send`, use the Discord channel or thread ID as `recipient`. Discord does not use a subject line; if one is provided it is prefixed into the outbound message body.

---

## Agent REST API

KoreAgent communicates with KoreComms exclusively via REST:

| Endpoint | Method | Description |
|---|---|---|
| `/api/send` | POST | Start a new outbound message on any interface. Body: `{ interface_id, recipient, subject, content }` |
| `/api/conversation/{id}` | GET | Return the full conversation thread as JSON (used by the live chat UI). |
| `/api/conversation/{id}/detail` | GET | Return local conversation metadata, current KC thread, events, and sync status in one response. |
| `/api/conversation/{id}/send` | POST | Append a human message to the linked agent conversation. Body: `{ content, if_missing? }` where `if_missing` is `abort` or `recreate`. |
| `/status` | GET | Health check — returns version and queue depth. |

---

## Message Lifecycle

```
[External Source / Human]
         │
         ▼
      RECEIVED         ← arrives from an interface or chat compose bar
         │
         ▼
   KC EVENTED         ← KoreChat raises `response_needed`
         │
         ▼
      AGENT WRITES DRAFT ← KoreAgent appends outbound draft
         │
         ▼
   OUTBOUND_READY     ← KoreComms claims event and routes the reply externally
```

Only one message is in `agent_processing` for a conversation at a time. KoreChat manages that coordination through its events table.

---

## WebUI Pages

| Path | Description |
|---|---|
| `/` | Conversation list — click any row to open the chat view |
| `/conversation/{id}` | Full chat view with live updates and compose bar |
| `/compose` | Inject a synthetic inbound message (Manual interface) |
| `/connections` | Add / edit / remove interface connections |
| `/state` | Override message state (debugging) |
| `/activity` | Agent activity log |

---

## Adding a New Interface Type

1. Create `app/interfaces/mytype/` and implement `BaseInterface` there (`poll`, `send_reply`, `send_new`)
2. Register it in `app/interfaces/common/registry.py`: `REGISTRY["mytype"] = MyTypeInterface`
3. Add any credential fields to the connection edit form in `connection_edit.html`

No changes to KoreComms routing logic are required.

---

## Related Repos

- [KoreAgent](../KoreAgent) — the agent that processes KoreChat events
- [KoreData](../KoreData) — data provider used alongside the agent
