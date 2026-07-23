# KoreStack

KoreStack is a local-first AI agent suite. One command line starts the whole system: an agent you talk to, a document editor, a spreadsheet, a diagram tool, a code editor, a data library, web feeds, a reference encyclopedia, and a communications hub - all running locally, all inter-connected.

It has no browser extensions, installed services or opaque config.

## The Suite

| Service | What it does |
|---|---|
| **KoreAgent** | The main interface. Chat with the agent, watch it work, manage scheduled tasks. |
| **KoreChat** | Conversation management - inspect and control the agent's conversation history. |
| **KoreData** | Data services: RSS feeds, a book library, a Wikipedia-scale reference encyclopedia, and a RAG chunk store. |
| **KoreDocs** | Document tools: a markdown editor, spreadsheet, and diagram editor, backed by a file manager. |
| **KoreCode** | A code editor for browsing and editing the workspace files directly in the browser. |
| **KoreComms** | External messaging hub - route inbound messages from email and other channels to the agent. |
| **KoreStack** | The control plane - start, stop, and monitor every service from one landing page. |

## Getting Started

**First time setup** - create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Then start the suite:

```powershell
python .\main.py
```

Then open the suite landing page:

```text
http://127.0.0.1:9600/
```

All services start automatically. Each one has its own port and its own tab in the top bar - click any service to go straight to it.

## Stopping and Restarting

Press `Ctrl+C` in the terminal to stop everything cleanly.

To restart or stop individual services without taking down the whole suite, use the service cards on the KoreStack landing page.

## Ports

| Service | Port |
|---|---|
| KoreStack | 9600 |
| KoreAgent | 9601 |
| KoreChat | 9602 |
| KoreData | 9603 |
| KoreComms | 9609 |
| KoreDocs | 9610 |
| KoreCode | 9611 |

## Workspace Layout

- `KoreAgent/` - agent runtime, skills, and task scheduler
- `KoreChat/` - conversation state and history
- `KoreData/` - feeds, library, reference, and RAG
- `KoreDocs/` - document, spreadsheet, and diagram editors
- `KoreCode/` - in-browser code editor
- `KoreComms/` - external messaging
- `KoreStack/` - suite landing page and control plane
- `config/` - suite configuration (`korestack_config.json` and `llm_config.json`)
- `datacontrol/` - service-owned, structured runtime data (see below)
- `datauser/` - unstructured user files; freely navigable by the agent's file access skill

## Data Layout

KoreStack separates **structured service data** from **unstructured user files**.

**`datacontrol/`** is owned by the services. Each service has a named subfolder for its databases and runtime state:

| Folder | Owner | Contents |
|---|---|---|
| `datacontrol/koreagent/` | KoreAgent | task queue state |
| `datacontrol/korechat/` | KoreChat | conversation database and logs |
| `datacontrol/korecomms/` | KoreComms | messaging database and interface state |
| `datacontrol/koredata/` | KoreData | sub-service databases (Feeds, Library, RAG, Reference) |
| `datacontrol/koredocs/` | KoreDocs | document index database |
| `datacontrol/logs/` | All services | rotating log files, one subfolder per service |
| `datacontrol/schedules/` | KoreAgent | task schedule definitions |

**`datauser/`** is unstructured and user-facing. It is the agent's writable workspace - notes, documents, spreadsheets, exports, and any files created or managed during a session. No service owns a specific subfolder here.
