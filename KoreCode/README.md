# KoreCode

KoreCode is the browser-based code workspace inside the suite. It combines a local file editor with workspace indexing, code-aware navigation, work items, and AI-assisted coding workflows.

## Why it exists

KoreStack needs a code surface that is aware of the local repository, easy to inspect from the browser, and tightly integrated with the rest of the suite. KoreCode is not trying to replace a full desktop IDE. It is the suite-native editor and code task surface.

## What it does

- Browses and edits files from the configured workspace root
- Builds and serves a workspace index for files and symbols
- Exposes browser flows for work items, edit proposals, and chat-driven coding actions
- Provides a local API for code reads, writes, and targeted Python-aware edits

## UI and workflow

KoreCode uses the shared suite shell and a split workspace layout.

- left: workspace explorer
- center: editor surface and open-file tabs
- optional AI or task surfaces beside the editor

The intended direction is task-centric coding rather than a plain file editor: inspect, propose, edit, validate, and review inside one browser workspace.

## How to run it

Normally you start KoreCode through the suite root:

```powershell
python .\main.py
```

To run KoreCode on its own:

```powershell
python .\KoreCode\main.py
```

## Install and configuration

- Install shared dependencies from the repo root with `pip install -r requirements.txt`
- KoreCode expects the workspace root and related paths to resolve through the shared suite configuration
- Browser shell assets come from `KoreUI/UIElements/`

## New user notes

- Start with the file tree and workspace index rather than the AI features first
- Expect KoreCode to be scoped to the KoreStack workspace, not arbitrary multi-root editing
- Generated workspace inventories should be treated as disposable artifacts rather than long-lived documentation

## Troubleshooting

| Problem | What to check |
|---|---|
| Files are missing from the browser tree | Confirm the resolved workspace root points at the expected checkout |
| Syntax or symbol features look stale | Rebuild the workspace index from the UI or API |
| Browser chrome is missing | Confirm shared assets from `KoreUI/UIElements/` are being served |
| AI-assisted flows fail | Check the configured agent or tool endpoint those flows depend on |
