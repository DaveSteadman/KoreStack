# config

Checked-in configuration for the suite. Services read it through `KoreCommon`; nothing here is started by hand.

| File | Purpose |
|---|---|
| `korestack_config.json` | Authoritative suite config: `paths` (including `dataroot`), `services.<name>` host, port and enablement, and MCP endpoints |
| `koreagent_config.json` | KoreAgent bootstrap: LLM host URL, chat model, System One decision model, and context window |
| `koreliveweb_config.json` | KoreLiveWeb search provider selection, provider settings, and optional API key |

## Ports

| Port | Service | Port | Service |
|---|---|---|---|
| 29600 | korestack | 29610 | koredocs |
| 29601 | koreagent | 29611 | korecode |
| 29602 | korechat | 29612 | korescrape |
| 29603 | koredatagateway | 29613 | koreliveweb |
| 29604 | korefeed | 29615 | korecron |
| 29605 | korelibrary | 29616 | koretest2 |
| 29606 | korerag | 29617 | koreagentnetwork |
| 29607 | korereference | 29618 | koreunittest |
| 29608 | koregraph | | |
| 29609 | korecomms | | |

## Data root

`paths.dataroot` holds `datacontrol/` (service state, test cases) and `datauser/` (user files). Override with `KORE_SUITE_DATAROOT` or `KORE_SUITE_DATACONTROL`.
