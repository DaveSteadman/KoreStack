# KoreChat

KoreChat is the suite conversation-state service. It is the canonical store for conversations, messages, and run events shared across KoreAgent and KoreComms.

## Why it exists

KoreAgent should be able to reason and act without owning durable thread storage itself. KoreChat separates long-lived session history from the agent runtime and provides a stable local API for the rest of the suite.

## What it does

- Stores conversations, message history, and event streams
- Exposes FastAPI endpoints for conversation, message, and event operations
- Provides the browser UI used to inspect threads and activity
- Acts as the coordination surface for message-driven work between the agent and comms layers

## Data and API shape

KoreChat is the canonical durable store for:

- conversation metadata
- append-only message history
- event-queue style coordination records between services
- conversation-scoped session fields such as scratchpad-style state and input history

Its main service contract is:

- conversation CRUD and lookup
- message append and history access
- event claim and completion flow for cooperating services
- `/status` for health and `/ui` for inspection

## How to run it

Normally you start KoreChat through the suite root:

```powershell
python .\main.py
```

To run KoreChat on its own:

```powershell
python .\KoreChat\main.py
```

Open the configured KoreChat UI at `/ui` on the configured port.

## Install and configuration

- Install shared dependencies once from the repo root with `pip install -r requirements.txt`
- KoreChat uses suite configuration from `config/korestack_config.json`
- Shared paths resolve through `KoreCommon/suite_paths.py`, so the configured data root must exist and be writable

## Troubleshooting

| Problem | What to check |
|---|---|
| KoreChat will not bind | Confirm `services.korechat.port` is free in `config/korestack_config.json` |
| Conversations appear missing | Verify the configured data root is the same one the rest of the suite is using |
| Another service cannot reach KoreChat | Check the configured host and the KoreChat connection URL used by the caller |
| UI loads but looks incomplete | Confirm shared assets from `KoreUI/UIElements/` are being served correctly |
