# datacontrol/KoreGraph

Processing scripts for KoreGraph — standalone Python scripts that build and
maintain the knowledge graph database (`datacontrol/koredata/Graph/graph.db`).

These scripts run outside the KoreGraph service, communicating with it through
its HTTP API at port 8626. They are designed to run over long periods without
supervision, following the same pattern as `datacontrol/koredata/RAG/ingest_hansard.py`.

---

## How to run

Each script is a standalone Python file. Run from the repo root with the `.venv`
interpreter active:

```
.\.venv\Scripts\python.exe datacontrol\KoreGraph\import_graph_refs.py
.\.venv\Scripts\python.exe datacontrol\KoreGraph\import_graph_refs.py --limit 200
.\.venv\Scripts\python.exe datacontrol\KoreGraph\import_graph_refs.py --dry-run
```

Scripts can also be launched from the **Processing** tab in the KoreGraph UI at
`http://localhost:8626/ui/processing`.

---

## Companion descriptor files

Each `.py` script has a sibling `.json` descriptor. The descriptor tells the
Processing UI the script's name, description, configurable arguments, and
records the last run timestamp/status. The server writes back `last_run`,
`last_status`, and `last_log` after each run.

---

## Scripts

| Script | Purpose |
|--------|---------|
| `import_graph_refs.py` | Phase 1 — bulk-import wikilinks + infobox facts from KoreReference (no LLM, fast) |
| `extract_graph_books.py` | Phase 2 — LLM-based triple extraction from KoreLibrary science/history books (slow) |

---

## Log files

Each run writes a timestamped log to `datacontrol/KoreGraph/logs/`.
Logs are streamed live in the Processing UI.

---

## Adding a new script

1. Create `yourscript.py` in this directory
2. Create `yourscript.json` alongside it using this structure:

```json
{
  "id": "yourscript",
  "name": "Human-readable name",
  "description": "What this script does.",
  "script": "yourscript.py",
  "args": [
    { "flag": "--limit", "type": "int", "default": 0, "help": "Max items (0=all)" },
    { "flag": "--dry-run", "type": "bool", "default": false, "help": "Count only" }
  ],
  "last_run": null,
  "last_status": null,
  "last_log": null
}
```

3. The Processing tab discovers it automatically — no server restart needed.
