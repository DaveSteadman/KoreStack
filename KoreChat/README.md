# KoreChat

KoreChat is the conversation-state service. It is the canonical durable store of conversations, messages, and events shared by KoreAgent and KoreComms.

## Role in the suite

KoreAgent reasons and acts without owning thread storage. KoreComms turns external messages into KoreChat conversations and events, and delivers replies the agent has marked ready. KoreCron and the browser UIs also read and write threads here.

## Architecture

| Element | Responsibility |
|---|---|
| Conversations | Metadata, external-id lookup, input history, session fields |
| Messages | Append-only history per conversation |
| Events | Queue-style records that cooperating services claim and complete |
| Browser UI | Thread and activity inspection, with a live `/stream` |

## Key API

| Endpoint | Purpose |
|---|---|
| `/api/conversations` | Create, list, get, update, delete; `/by-external-id/{id}` lookup |
| `/api/conversations/{id}/messages`, `/turns` | Append and read messages |
| `/api/events`, `/api/events/next`, `/api/events/{id}/complete` | Event post, claim, and completion |
| `/status`, `/ui` | Health and browser UI |

Routes without the `/api` prefix are retained for older callers.

## Data

SQLite store under `<dataroot>/datacontrol/korechat`.

## Troubleshooting

| Problem | What to check |
|---|---|
| Agent history missing | KoreAgent and KoreChat use the same data root |
| Events never complete | The consumer is claiming through `/api/events/next` and completing them |
| UI shows nothing | KoreChat is healthy on the landing page |
