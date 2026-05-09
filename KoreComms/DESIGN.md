
# KoreComms

## Purpose

KoreComms is the external messaging hub for the suite. It manages channel interfaces (Gmail and manual), tracks conversation mappings, and routes inbound and outbound traffic through KoreChat as the canonical conversation state service.

---

## System Context

KoreComms collaborates primarily with KoreChat and KoreAgent:

| Service | Role |
|---|---|
| **KoreComms** | Channel adapters, operator messaging UI, delivery orchestration |
| **KoreChat** | Canonical conversation/messages/events state |
| **KoreAgent** | Agent runtime that consumes and produces KoreChat events |

KoreComms does not own canonical thread history. It stores interface and external-message metadata locally, while message and event flow coordination is driven through KoreChat APIs.

---

## Runtime Model

1. Inbound channel traffic is normalized by an interface adapter.
2. KoreComms resolves or creates the linked KoreChat conversation.
3. KoreComms appends inbound messages and raises response events in KoreChat.
4. Agent-generated outbound content is read from KoreChat/event flow and delivered back through the source interface.
5. Delivery state is written back to KoreChat and local activity logs.

This model replaces the older direct dequeue/reply API pattern.

---

## API Surface

### Health and shared assets

| Method | Path | Description |
|---|---|---|
| `GET` | `/status` | Service health payload used by suite orchestration |
| `GET` | `/suite-config.js` | Suite URLs/config for shell/runtime links |
| `GET` | `/ui-elements/assets/{asset_path}` | Shared UIElements assets |

### JSON API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/send` | Start new outbound conversation via selected interface |
| `GET` | `/api/conversation/{conv_id}` | Lightweight conversation JSON |
| `GET` | `/api/conversation/{conv_id}/detail` | Conversation detail JSON for UI refresh |
| `GET` | `/api/events/stream` | Event stream for live conversation updates |
| `POST` | `/api/conversation/{conv_id}/send` | Send message into an existing conversation |

### HTML UI routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Conversation list |
| `GET` | `/compose` | Manual compose/inject form |
| `POST` | `/compose` | Submit manual injected inbound message |
| `GET` | `/connections` | Interface connection list |
| `GET` | `/connections/new` | New connection form |
| `POST` | `/connections/new` | Create connection |
| `GET` | `/connections/{iface_id}` | Edit/view connection |
| `POST` | `/connections/{iface_id}` | Save connection updates |
| `POST` | `/connections/{iface_id}/delete` | Delete connection |
| `GET` | `/connections/{iface_id}/gmail-authorize` | Start Gmail OAuth flow |
| `GET` | `/gmail-callback` | Complete Gmail OAuth callback |
| `GET` | `/activity` | Activity log UI |
| `GET` | `/conversation/{conv_id}` | Conversation thread page |
| `POST` | `/conversation/{conv_id}/delete` | Delete conversation |
| `POST` | `/conversation/{conv_id}/send` | Send from HTML thread composer |

---

## Interfaces

### Gmail

- OAuth-backed channel with polling for inbound messages.
- Uses external thread IDs and message IDs for dedupe and reply routing.
- Outbound replies/new messages are sent through Gmail APIs and linked back to KoreChat records.

### Manual

- Local operator-only interface for testing and controlled message injection.
- Messages are entered through the Web UI and flow through the same KoreChat-backed path as other interfaces.

---

## Data Ownership

| Store | Owned by | Notes |
|---|---|---|
| Canonical conversation history | KoreChat | Conversations, messages, events |
| Interface definitions | KoreComms SQLite | Type, credentials/settings, enabled state |
| External message mapping | KoreComms SQLite | External IDs and delivery correlation |
| Operator activity log | KoreComms SQLite/logs | Auditing and troubleshooting context |

---

## Configuration

KoreComms reads suite config from top-level config files and environment overrides.

| Key | Typical suite value | Notes |
|---|---|---|
| `host` | `127.0.0.1` | From suite network host when launched via KoreStack |
| `port` | `8625` | Suite default from config/default.json services.comms.port |
| `korechat_url` | `http://127.0.0.1:8630` | Suite connection target |
| `poll_interval` | `60` | Interface polling cadence |

Standalone fallback defaults may differ (for example port 8900), but suite-mode values from config/default.json are authoritative for the integrated stack.

---

## Non-Functional Expectations

- Sequential and deterministic processing for each interface poll cycle.
- Durable handoff behavior through KoreChat events.
- Clear operator visibility through activity logs and thread UI.
- Extensible adapter model for adding channels without redesigning core routing.

