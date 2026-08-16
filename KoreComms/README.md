# KoreComms

KoreComms is the external communications subsystem. It owns the integration surface for inbound and outbound messaging channels and bridges those conversations into the local KoreChat and KoreAgent workflow.

## Why it exists

The agent should not talk directly to Gmail, Discord, or other external systems. KoreComms isolates that risk, keeps credentials and adapter logic in one place, and converts channel activity into local suite events.

## What it does

- Polls or receives messages from supported interfaces
- Stores local conversation metadata and interface state
- Bridges inbound messages into KoreChat-backed agent conversations
- Delivers outbound messages once the agent marks them ready
- Provides browser pages for conversations, connections, compose, and activity

## Service contract

KoreComms owns transport adapters and delivery state, while KoreChat remains the canonical thread record.

- inbound channel traffic is translated into local conversations and events
- outbound delivery happens only after the agent writes a draft and marks it ready
- browser pages exist for operators to inspect conversations, configure interfaces, and inject manual messages

## How to run it

Normally you start KoreComms through the suite root:

```powershell
python .\main.py
```

To run KoreComms on its own:

```powershell
python .\KoreComms\main.py
```

## Install and configuration

- Install shared dependencies from the repo root with `pip install -r requirements.txt`
- KoreComms reads host, port, and related service URLs from `config/korestack_config.json`
- Interface-specific credentials are configured through the subsystem itself and stored locally
- KoreComms depends on KoreChat for canonical thread history and event coordination

## New user notes

- Start with the manual interface first before wiring external providers
- Add one connection at a time and confirm send and receive flows before enabling more channels
- Treat external secrets and OAuth credentials as local environment state, not repository defaults
- An SFTP file connection is output-only: every delivery replaces its configured absolute remote file.
  Its server host key must be present in the service account's `known_hosts` file.

## Troubleshooting

| Problem | What to check |
|---|---|
| Connection polling appears idle | Confirm the interface is configured, enabled, and has valid credentials |
| Replies do not get delivered | Check KoreChat connectivity and whether outbound events are being produced |
| UI works but external channel traffic does not | Verify network access, credentials, and channel identifiers for that interface |
| Service starts but cannot bind | Confirm `services.korecomms.port` is free |

